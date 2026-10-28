from collections import OrderedDict
import time
import html
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QSize, QTimer, Signal, QRect, QPoint
from PySide6.QtGui import QColor, QPainter, QPixmap, QFont
from PySide6.QtWidgets import QListView, QStyledItemDelegate, QStyle, QAbstractItemView
from .images import make_thumbnail, decode_preview
from .workers import Tasks

class GalleryModel(QAbstractListModel):
    """Virtual row positions with a bounded LRU of metadata pages."""
    changed = Signal(int)
    failed = Signal(str)
    PAGE_SIZE = 256
    MAX_PAGES = 8

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.pages = OrderedDict()
        self.total = 0
        self.scope = None
        self.search = ''
        self.sort = 'newest'
        self.source_filter = None
        self.generation = 0
        self.loading = False
        self.pending_pages = {}
        self.failed_pages = set()
        self.wanted_pages = OrderedDict()
        self.tasks = Tasks(self, 1)
        self.dispatch = QTimer(self)
        self.dispatch.setSingleShot(True)
        self.dispatch.timeout.connect(self._dispatch_pages)

    @property
    def rows(self):
        """Resident records only; positions are accessed through row_at()."""
        return [row for page in self.pages.values() for row in page]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.total

    def row_at(self, position, request=True):
        if not 0 <= position < self.total:
            return None
        page_number, within = divmod(position, self.PAGE_SIZE)
        page = self.pages.get(page_number)
        if page is not None:
            self.pages.move_to_end(page_number)
            return page[within] if within < len(page) else None
        if request and page_number not in self.pending_pages and page_number not in self.failed_pages:
            self.wanted_pages[page_number] = None
            self.wanted_pages.move_to_end(page_number)
            while len(self.wanted_pages) > 8:
                self.wanted_pages.popitem(last=False)
            if not self.dispatch.isActive():
                self.dispatch.start(0)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.row_at(index.row())
        if role == Qt.ItemDataRole.DisplayRole:
            return row['name'] if row else '불러오는 중…'
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.ToolTipRole and row:
            return '<span>'+html.escape(row['name'])+'<br>'+html.escape(row['path'])+'</span>'

    def reload(self, scope=None, search='', sort='newest', source_filter=None):
        self.generation += 1
        generation = self.generation
        for job in self.pending_pages.values():
            job.cancelled.set()
        self.pending_pages.clear()
        self.failed_pages.clear()
        self.wanted_pages.clear()
        self.dispatch.stop()
        self.scope, self.search, self.sort = scope, search, sort
        self.source_filter = source_filter
        self.beginResetModel()
        self.pages.clear()
        self.total = 0
        self.endResetModel()
        self.loading = True
        def done(result):
            if generation != self.generation:
                return
            rows, total = result
            self.pending_pages.pop(-1,None)
            self.beginResetModel()
            self.total = total
            self.pages[0] = rows
            self.endResetModel()
            self.loading = False
            self.changed.emit(total)
        def error(message):
            if generation == self.generation:
                self.loading = False
                self.failed.emit(message)
        job = self.tasks.submit(lambda _: self.db.query(scope, search, sort, source_filter=source_filter), done, error)
        self.pending_pages[-1] = job

    def _dispatch_pages(self):
        while self.wanted_pages and len([p for p in self.pending_pages if p >= 0]) < 2:
            number, _ = self.wanted_pages.popitem(last=True)
            if number in self.pages or number in self.pending_pages:
                continue
            generation = self.generation
            scope, search, sort = self.scope, self.search, self.sort
            source_filter = self.source_filter
            def done(result, number=number, generation=generation):
                if generation != self.generation:
                    return
                self.pending_pages.pop(number, None)
                rows, _ = result
                self.pages[number] = rows
                self.pages.move_to_end(number)
                while len(self.pages) > self.MAX_PAGES:
                    self.pages.popitem(last=False)
                first = number*self.PAGE_SIZE
                if rows:
                    self.dataChanged.emit(self.index(first), self.index(first+len(rows)-1))
                self.changed.emit(self.total)
                self._dispatch_pages()
            def error(message, number=number, generation=generation):
                if generation != self.generation:
                    return
                self.pending_pages.pop(number, None)
                self.failed_pages.add(number)
                self.failed.emit(message)
                self._dispatch_pages()
            self.pending_pages[number] = self.tasks.submit(
                lambda _, number=number, scope=scope, search=search, sort=sort, source_filter=source_filter:
                    self.db.query(scope, search, sort, number*self.PAGE_SIZE, self.PAGE_SIZE, count=False, source_filter=source_filter),
                done, error)

    def close_tasks(self):
        self.generation += 1
        self.dispatch.stop()
        self.tasks.stop()

    def canFetchMore(self, parent=QModelIndex()):
        # All positions already exist; the view requests missing pages through data().
        return False

class GalleryView(QListView):
    def __init__(self, model, cache, parent=None):
        super().__init__(parent)
        self.setModel(model)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(80)
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumbs = OrderedDict()
        self.errors = set()
        self.pending = {}
        self.cache = cache
        self.authorize = None
        self.tasks = Tasks(self, 2)
        self.edge = 216
        self.setItemDelegate(PhotoDelegate(self))
        self.anchor_timer = QTimer(self)
        self.anchor_timer.setInterval(16)
        self.anchor_timer.timeout.connect(self._restore_anchor)
        self.anchor = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.load_visible)
        self.verticalScrollBar().valueChanged.connect(self.schedule)
        model.rowsInserted.connect(self.schedule)
        model.dataChanged.connect(self.schedule)
        model.modelReset.connect(self.reset_cache)
        self.set_size(216)
    def reset_cache(self):
        self.anchor_timer.stop()
        self.anchor = None
        for _,job in self.pending.values():
            job.cancelled.set()
        self.pending.clear()
        self.thumbs.clear()
        self.errors.clear()
        self.schedule()
    def set_size(self, edge):
        self.edge = edge
        self.setGridSize(QSize(edge+20, edge+62))
        self.schedule()
    def schedule(self, *_):
        # Throttle rather than debounce: sustained scrolling must still load visible work.
        if not self.timer.isActive():
            self.timer.start(16)
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule()
    def load_visible(self):
        model = self.model()
        # Find rows intersecting the viewport using layout coordinates; only visible jobs enter the pool.
        rect = self.viewport().rect()
        start = self.indexAt(rect.topLeft()+QPoint(12,12)).row()
        if start < 0:
            start = max(0, int(self.verticalScrollBar().value()/max(1,self.gridSize().height())) * max(1,self.viewport().width()//self.gridSize().width()))
        columns = max(1, self.viewport().width()//self.gridSize().width())
        count = columns * (self.viewport().height()//self.gridSize().height()+3)
        visible = []
        for rownum in range(start, min(model.rowCount(), start+count)):
            row = model.row_at(rownum)
            if row is not None:
                visible.append(row)
        desired = {row['sha256'] for row in visible}
        for key,(_,job) in list(self.pending.items()):
            if key not in desired:
                job.cancelled.set()
                self.pending.pop(key,None)
        for row in visible:
            key = row['sha256']
            if key in self.thumbs or key in self.pending or key in self.errors:
                continue
            if len(self.pending) >= 8:
                break
            token = object()
            path, cache = row['path'], self.cache
            def done(image, key=key, token=token):
                current = self.pending.get(key)
                if current is None or current[0] is not token:
                    return
                self.pending.pop(key,None)
                pix = QPixmap.fromImage(image)
                if not pix.isNull():
                    self.thumbs[key] = pix
                    while len(self.thumbs) > 96:
                        self.thumbs.popitem(last=False)
                self.viewport().update()
                self.schedule()
            def error(message, key=key, token=token):
                current = self.pending.get(key)
                if current is None or current[0] is not token:
                    return
                self.pending.pop(key,None)
                self.errors.add(key)
                self.viewport().update()
                self.schedule()
            def decode(_, path=path, cache=cache, key=key, row=dict(row)):
                if self.authorize is not None and not self.authorize(row):
                    raise ValueError('이 사진의 표시 권한을 먼저 확인하세요.')
                return decode_preview(make_thumbnail(path,cache,key),512)
            job = self.tasks.submit(decode,done,error)
            self.pending[key] = (token,job)
    def restore_anchor(self, position, y_offset, selected_position=None):
        self.anchor = position,y_offset,selected_position,time.monotonic()+5
        self.anchor_timer.start()
        self._restore_anchor()

    def _restore_anchor(self):
        if self.anchor is None:
            return
        position,y_offset,selected_position,deadline = self.anchor
        if time.monotonic()>deadline or not 0<=position<self.model().rowCount():
            self.anchor_timer.stop()
            self.anchor = None
            return
        index = self.model().index(position)
        if not self.visualRect(index).isValid():
            return
        if selected_position is not None:
            self.model().row_at(selected_position)
            self.setCurrentIndex(self.model().index(selected_position))
        self.scrollTo(index,QAbstractItemView.ScrollHint.PositionAtTop)
        self.verticalScrollBar().setValue(self.verticalScrollBar().value()+self.visualRect(index).top()-y_offset)
        self.anchor_timer.stop()
        self.anchor = None
        self.schedule()

    def close_tasks(self):
        self.anchor_timer.stop()
        self.timer.stop()
        self.tasks.stop()

class PhotoDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        view = self.parent()
        if row is None:
            painter.save()
            painter.fillRect(option.rect.adjusted(6,6,-6,-6), QColor('#1c1d23'))
            painter.setPen(QColor('#727681'))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, '불러오는 중…')
            painter.restore()
            return
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        rect = option.rect.adjusted(6, 6, -6, -6)
        painter.setPen(QColor('#777fda' if selected else '#292b32'))
        painter.setBrush(QColor('#2a2b3b' if selected else '#1c1d23'))
        painter.drawRoundedRect(rect, 8, 8)
        photo = QRect(rect.x()+5, rect.y()+5, rect.width()-10, view.edge)
        key = row['sha256']
        pix = view.thumbs.get(key)
        if pix:
            view.thumbs.move_to_end(key)
            size = pix.size().scaled(photo.size(), Qt.AspectRatioMode.KeepAspectRatio)
            dst = QRect(0, 0, size.width(), size.height())
            dst.moveCenter(photo.center())
            painter.drawPixmap(dst, pix)
        else:
            painter.setPen(QColor('#727681'))
            painter.drawText(photo, Qt.AlignmentFlag.AlignCenter, '파일 확인 필요' if key in view.errors else 'IMAGE')
        painter.setFont(QFont('Segoe UI', 9))
        painter.setPen(QColor('#e3e5ed'))
        text_rect = QRect(rect.x()+10, photo.bottom()+10, rect.width()-20, 18)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft, painter.fontMetrics().elidedText(row['name'], Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.setPen(QColor('#989ca9'))
        painter.drawText(text_rect.translated(0,20), Qt.AlignmentFlag.AlignLeft, f"{row['width']:,} × {row['height']:,}  ·  {row['format']}" )
        if row['favorite']:
            painter.setPen(QColor('#d1c2ff'))
            painter.drawText(photo.adjusted(6,6,-6,-6), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight, '★')
        painter.restore()
    def sizeHint(self, option, index):
        return self.parent().gridSize()
