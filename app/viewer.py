from PySide6.QtCore import Qt, QTimer, Signal, QRectF
from PySide6.QtGui import QPainter, QShortcut, QKeySequence
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QGraphicsView, QGraphicsScene, QWidget, QGraphicsItem, QMenu)
from .images import decode_preview
from .cache import ImageCache
from .workers import Tasks


class ImageItem(QGraphicsItem):
    """Retain the owned QImage without a second full-resolution pixmap conversion.

    Paint only the exposed source rectangle. This is viewport-limited painting,
    not region decoding: the decoder still owns one complete scaled/full image.
    """
    def __init__(self,image):
        super().__init__()
        self.image=image
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption,True)

    def boundingRect(self):
        return QRectF(0,0,self.image.width(),self.image.height())

    def paint(self,painter,option,widget=None):
        exposed=option.exposedRect.intersected(self.boundingRect())
        painter.drawImage(exposed,self.image,exposed)


class Canvas(QGraphicsView):
    zoomed = Signal(float)
    activity = Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.item = None
        self.source_width = 1
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setStyleSheet('border:0;background:#101115;')
        self.fit_mode = 'fit'
        self.setMouseTracking(True)

    def show_image(self, image, source_width=None):
        self.scene().clear()
        self.item = ImageItem(image)
        self.scene().addItem(self.item)
        self.source_width = source_width or image.width()
        self.scene().setSceneRect(self.item.boundingRect())
        self.fit()

    def physical_zoom(self):
        if not self.item:
            return 1.0
        return self.transform().m11()*self.devicePixelRatioF()*self.item.image.width()/self.source_width

    def fit(self, mode='fit'):
        if not self.item:
            return
        if mode == 'fit':
            self.fitInView(self.item, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.resetTransform()
            bounds = self.item.boundingRect()
            scale = ((self.viewport().width()-4)/bounds.width() if mode == 'width'
                     else (self.viewport().height()-4)/bounds.height())
            self.scale(scale, scale)
        self.fit_mode = mode
        self.zoomed.emit(self.physical_zoom())

    def actual(self):
        if self.item:
            self.resetTransform()
            factor = self.source_width/self.item.image.width()/self.devicePixelRatioF()
            self.scale(factor, factor)
            self.fit_mode = None
            self.zoomed.emit(self.physical_zoom())

    def zoom(self, factor):
        scale = self.physical_zoom()*factor
        if .005 <= scale <= 32:
            self.scale(factor, factor)
            self.fit_mode = None
            self.zoomed.emit(self.physical_zoom())

    def wheelEvent(self, event):
        self.activity.emit()
        self.zoom(1.2 if event.angleDelta().y()>0 else 1/1.2)
        event.accept()

    def mouseMoveEvent(self, event):
        self.activity.emit()
        super().mouseMoveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit(self.fit_mode)


class Viewer(QDialog):
    favorite_changed = Signal(str, bool)
    image_ready = Signal(str)
    related_requested = Signal(str)
    upscale_requested = Signal(str)
    MAX_RECORDS = 768

    def __init__(self, rows, position, parent=None, mode='fit', load_more=None,
                 total=None, base_index=0, related_repo=None, related_cache=None, row_resolver=None):
        super().__init__(parent)
        self.setWindowTitle('감상 · K-IDOL ARCHIVE')
        self.resize(1200, 820)
        self.rows, self.position = rows, position
        self.base_index = base_index
        self.load_more = load_more
        self.total = total if total is not None else len(rows)
        self.fetching = False
        self.generation = 0
        self.closed = False
        self.tasks = Tasks(self, 1)
        self.prefetch_tasks = Tasks(self, 1)
        self.prefetch_jobs = {}
        self.prefetch_failures = set()
        self.cache = ImageCache()
        self.current_job = None
        self.displayed_id = None
        self.mode = mode
        self.row_resolver = row_resolver
        self.last_cache_hit = False
        self.related_strip = None
        self.strip_auto_hidden = False
        self.strip_remote_hidden = False
        self.prefetch_timer = QTimer(self)
        self.prefetch_timer.setSingleShot(True)
        self.prefetch_timer.timeout.connect(self.prefetch_neighbors)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(2200)
        self.hide_timer.timeout.connect(self.hide_chrome)
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.title)
        self.controls = QWidget()
        self.bar = QHBoxLayout(self.controls)
        self.bar.setContentsMargins(0,0,0,0)
        self.zoom_label = QLabel()
        self.bar.addWidget(self.zoom_label,1)
        self.buttons = {}
        for label, action in [('‹', lambda: self.step(-1)), ('›', lambda: self.step(1)),
                              ('−', lambda: self.canvas.zoom(1/1.2)), ('＋', lambda: self.canvas.zoom(1.2)),
                              ('화면 맞춤', lambda: self.canvas.fit()),
                              ('원본 픽셀', self.actual), ('전체화면', self.fullscreen),
                              ('☆', self.favorite), ('미리보기', self.toggle_related_strip), ('연관 사진', self.related), ('AI 업스케일',lambda:self.upscale_requested.emit(self.rows[self.position]['id'])), ('닫기', self.close)]:
            button = QPushButton(label)
            if label=='화면 맞춤':
                button.setText('맞춤 ▾')
                menu=QMenu(button)
                menu.addAction('전체 이미지 맞춤 · Ctrl+0',lambda:self.canvas.fit())
                menu.addAction('너비 맞춤 · W',lambda:self.canvas.fit('width'))
                menu.addAction('높이 맞춤 · H',lambda:self.canvas.fit('height'))
                button.setMenu(menu)
            else:
                button.clicked.connect(action)
            self.bar.addWidget(button)
            self.buttons[label] = button
        layout.addWidget(self.controls)
        self.canvas = Canvas()
        self.canvas.zoomed.connect(lambda scale: self.zoom_label.setText(f'{scale*100:.1f}%'))
        self.canvas.activity.connect(self.show_chrome)
        layout.addWidget(self.canvas,1)
        if related_repo is not None and related_cache is not None:
            from .related_strip import RelatedStrip
            self.related_strip = RelatedStrip(related_repo,related_cache,self)
            self.related_strip.activated.connect(self.related_requested.emit)
            layout.addWidget(self.related_strip)
        self.buttons['미리보기'].setEnabled(self.related_strip is not None)
        self.buttons['미리보기'].setToolTip('연관 사진 필름스트립 표시/숨김')
        self.info = QLabel()
        self.info.setTextFormat(Qt.TextFormat.PlainText)
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.info)
        for key, action in [('Right', lambda:self.step(1)), ('Left', lambda:self.step(-1)),
                            ('F11',self.fullscreen), ('Ctrl+0',self.canvas.fit), ('Ctrl+1',self.actual),
                            ('W', lambda:self.canvas.fit('width')), ('H',lambda:self.canvas.fit('height'))]:
            shortcut = QShortcut(QKeySequence(key),self)
            shortcut.activated.connect(action)
        self.show_current(actual=self.mode=='actual')

    def key(self, row, actual=False):
        edge = max(row['width'],row['height']) if actual else 4096
        return row['sha256'], row['path'], edge

    def show_current(self, actual=False):
        if self.closed:
            return
        self.generation += 1
        generation = self.generation
        if self.current_job:
            self.current_job.cancelled.set()
        self.prefetch_timer.stop()
        row = self.rows[self.position]
        if self.related_strip and row.get('kind','asset')=='asset':
            if self.strip_remote_hidden:self.related_strip.show();self.strip_remote_hidden=False
            self.related_strip.set_seed(row['id'])
        elif self.related_strip:
            self.strip_remote_hidden=self.strip_remote_hidden or not self.related_strip.isHidden() or self.strip_auto_hidden
            self.related_strip.cancel();self.related_strip.seed=None;self.related_strip.list.clear();self.related_strip.hide();self.strip_auto_hidden=False
        self.buttons['AI 업스케일'].setEnabled(row.get('kind','asset')=='asset')
        self.buttons['연관 사진'].setEnabled(row.get('kind','asset')=='asset')
        self.buttons['미리보기'].setEnabled(self.related_strip is not None and row.get('kind','asset')=='asset')
        self.title.setText(f"{self.base_index+self.position+1} / {self.total}   {row['name']}")
        self.info.setText(f"불러오는 중…  ·  {row['width']:,} × {row['height']:,}\n{row['path']}")
        self.buttons['☆'].setText('★' if row['favorite'] else '☆')
        self.buttons['‹'].setEnabled(self.base_index+self.position>0 and not self.fetching)
        self.buttons['›'].setEnabled(self.base_index+self.position<self.total-1 and not self.fetching)
        if self.displayed_id != row['id']:
            self.canvas.scene().clear()
            self.canvas.item = None
        if self.row_resolver is not None:
            def resolved(value):
                if self.closed or generation!=self.generation:return
                self.rows[self.position]=value
                self._load_row(value,actual,generation)
            def failed(message):
                if not self.closed and generation==self.generation:self.info.setText('원본을 열 수 없습니다. 출처 연결·원본 이용 권한을 확인하세요.\n'+message)
            self.current_job=self.tasks.submit(lambda cancelled:self.row_resolver(row,cancelled),resolved,failed)
        else:self._load_row(row,actual,generation)

    def _load_row(self,row,actual,generation):
        key=self.key(row,actual)
        def done(image):
            if generation != self.generation or self.closed:
                return
            self.cache.put(key,image)
            self.canvas.show_image(image,row['width'])
            self.displayed_id = row['id']
            if actual:
                self.canvas.actual()
            limited = image.width()<row['width'] or image.height()<row['height']
            status = '빠른 미리보기 · 원본 픽셀에서 전체 해상도' if limited else '전체 해상도'
            if row.get('kind','asset')!='asset':status+=' · 보기 캐시 · 제공된 파일 기준'
            if row.get('preview_only'):status='제공된 미리보기 · 원본 해상도 미확인'
            elif row.get('provided_file_only'):status='사이트 제공 파일 · 크기 실측 · 최대 원본 여부 미확인'
            self.buttons['원본 픽셀'].setText('미리보기 픽셀' if row.get('preview_only') else ('파일 픽셀' if row.get('provided_file_only') else '원본 픽셀'))
            self.info.setText(f"{row['width']:,} × {row['height']:,}  ·  {row['format']}  ·  {status}\n{row['path']}")
            self.prefetch_timer.start(120)
            self.image_ready.emit(row['id'])
        def error(message):
            if generation == self.generation and not self.closed:
                self.info.setText('이미지를 열 수 없습니다. 갤러리에서 원본을 다시 연결하세요.\n'+message)
        image = self.cache.get(key)
        self.last_cache_hit = image is not None
        if image is not None:
            done(image)
        else:
            self.current_job = self.tasks.submit(lambda _:decode_preview(row['path'],key[2]),done,error)

    def prefetch_neighbors(self):
        if self.closed:
            return
        desired = {}
        for position in (self.position+1,self.position-1):
            if 0 <= position < len(self.rows):
                row = self.rows[position]
                if row.get('kind','asset')!='asset' or row.get('requires_review'):continue
                key = self.key(row)
                # Do not spend memory preloading full-sized large images or oversized previews.
                factor = min(1,4096/max(row['width'],row['height']))
                if row['width']*row['height']*factor*factor*4 <= self.cache.max_bytes//2:
                    desired[key] = row
        for key,job in list(self.prefetch_jobs.items()):
            if key not in desired:
                job.cancelled.set()
                self.prefetch_jobs.pop(key,None)
        for key,row in desired.items():
            if key in self.cache.items or key in self.prefetch_jobs or key in self.prefetch_failures:
                continue
            def done(image,key=key):
                self.prefetch_jobs.pop(key,None)
                if not self.closed:
                    self.cache.put(key,image)
            def error(message,key=key):
                self.prefetch_jobs.pop(key,None)
                self.prefetch_failures.add(key)
                while len(self.prefetch_failures)>128:
                    self.prefetch_failures.pop()
            def decode_neighbor(cancelled,row=dict(row)):
                value=self.row_resolver(row,cancelled) if self.row_resolver is not None else row
                return decode_preview(value['path'],4096)
            self.prefetch_jobs[key] = self.prefetch_tasks.submit(decode_neighbor,done,error)

    def actual(self):
        self.show_chrome()
        self.show_current(actual=True)

    def step(self,delta):
        if self.fetching or self.closed:
            return
        self.show_chrome()
        value = self.position+delta
        if 0 <= value < len(self.rows):
            self.position = value
            self.show_current(actual=self.mode=='actual')
            return
        absolute = self.base_index+value
        if not self.load_more or not 0 <= absolute < self.total:
            return
        self.fetching = True
        self.info.setText('사진 목록을 불러오는 중…')
        self.buttons['‹'].setEnabled(False)
        self.buttons['›'].setEnabled(False)
        offset = self.base_index+len(self.rows) if delta>0 else max(0,self.base_index-256)
        def done(result):
            self.fetching = False
            if self.closed:
                return
            extra,self.total = result
            if delta>0:
                self.position = len(self.rows)
                self.rows.extend(extra)
                if len(self.rows)>self.MAX_RECORDS:
                    trim = len(self.rows)-self.MAX_RECORDS
                    del self.rows[:trim]
                    self.base_index += trim
                    self.position -= trim
            else:
                # The first returned page may overlap if offset was clamped to zero.
                extra = extra[:self.base_index-offset]
                self.rows = extra+self.rows
                self.base_index = offset
                self.position = len(extra)-1
                if len(self.rows)>self.MAX_RECORDS:
                    del self.rows[self.MAX_RECORDS:]
            if extra:
                self.show_current(actual=self.mode=='actual')
            else:
                self.position = max(0,min(self.position,len(self.rows)-1))
                self.show_current(actual=self.mode=='actual')
        def error(message):
            self.fetching = False
            if not self.closed:
                self.info.setText('목록을 불러오지 못했습니다: '+message)
                self.buttons['‹'].setEnabled(self.base_index+self.position>0)
                self.buttons['›'].setEnabled(self.base_index+self.position<self.total-1)
        self.tasks.submit(lambda _:self.load_more(offset),done,error)

    def fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self.show_chrome()

    def show_chrome(self):
        if self.related_strip and self.strip_auto_hidden:
            self.strip_auto_hidden=False
            self.related_strip.show();self.related_strip.refresh()
        self.title.show()
        self.controls.show()
        self.info.show()
        self.canvas.viewport().unsetCursor()
        if self.isFullScreen():
            self.hide_timer.start()

    def hide_chrome(self):
        if self.isFullScreen():
            # Keep errors/loading visible; only hide controls after a successful image load.
            if self.canvas.item is None:
                return
            if self.related_strip and not self.related_strip.isHidden():
                self.strip_auto_hidden=True;self.related_strip.hide();self.related_strip.cancel()
            self.title.hide()
            self.controls.hide()
            self.info.hide()
            self.canvas.viewport().setCursor(Qt.CursorShape.BlankCursor)

    def toggle_related_strip(self):
        if self.related_strip:self.related_strip.toggle()
        self.show_chrome()

    def related(self):
        self.show_chrome()
        self.related_requested.emit(self.rows[self.position]['id'])

    def favorite(self):
        row = self.rows[self.position]
        row['favorite'] = not row['favorite']
        self.buttons['☆'].setText('★' if row['favorite'] else '☆')
        self.favorite_changed.emit(row['id'],bool(row['favorite']))

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.generation += 1
        self.prefetch_timer.stop()
        self.hide_timer.stop()
        if self.related_strip:self.related_strip.shutdown()
        self.tasks.stop()
        self.prefetch_tasks.stop()
        self.prefetch_jobs.clear()
        self.cache.clear()
        self.canvas.scene().clear()
        self.canvas.item=None
        self.displayed_id=None

    def done(self,result):
        self.shutdown()
        super().done(result)

    def closeEvent(self,event):
        from .upscale_dialog import UpscaleDialog
        dialogs=self.findChildren(UpscaleDialog)
        if any(d.busy and not d.closed for d in dialogs):
            for dialog in dialogs:dialog.close()
            event.ignore();QTimer.singleShot(100,self.close);return
        for dialog in dialogs:
            if not dialog.closed:dialog.close()
        self.shutdown()
        super().closeEvent(event)
