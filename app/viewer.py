from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QPainter, QShortcut, QKeySequence
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QGraphicsView, QGraphicsScene
from .images import decode_preview
from .workers import Tasks

class Canvas(QGraphicsView):
    zoomed = Signal(float)
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.item = None
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setStyleSheet('border:0;background:#101115;')
        self.fit_mode = True
    def show_image(self, image):
        self.scene().clear()
        self.item = self.scene().addPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self.item.boundingRect())
        self.fit()
    def fit(self):
        if self.item:
            self.fitInView(self.item, Qt.AspectRatioMode.KeepAspectRatio)
            self.fit_mode = True
            self.zoomed.emit(self.transform().m11())
    def zoom(self, factor):
        scale = self.transform().m11()*factor
        if .01 <= scale <= 32:
            self.scale(factor, factor)
            self.fit_mode = False
            self.zoomed.emit(self.transform().m11())
    def wheelEvent(self, event):
        self.zoom(1.2 if event.angleDelta().y()>0 else 1/1.2)
        event.accept()
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode:
            self.fit()

class Viewer(QDialog):
    favorite_changed = Signal(str, bool)
    def __init__(self, rows, position, parent=None, mode='fit', load_more=None, total=None):
        super().__init__(parent)
        self.setWindowTitle('감상 · K-IDOL ARCHIVE')
        self.resize(1200, 820)
        self.rows, self.position = rows, position
        self.load_more = load_more
        self.total = total if total is not None else len(rows)
        self.fetching = False
        self.generation = 0
        self.tasks = Tasks(self, 2)
        self.current_job = None
        self.mode = mode
        layout = QVBoxLayout(self)
        self.bar = QHBoxLayout()
        self.title = QLabel()
        self.bar.addWidget(self.title, 1)
        self.zoom_label = QLabel()
        self.bar.addWidget(self.zoom_label)
        self.buttons = {}
        for label, action in [('‹', lambda: self.step(-1)), ('›', lambda: self.step(1)), ('−', lambda: self.canvas.zoom(1/1.2)), ('＋', lambda: self.canvas.zoom(1.2)), ('화면 맞춤', lambda: self.canvas.fit()), ('원본 픽셀', self.actual), ('전체화면', self.fullscreen), ('☆', self.favorite), ('닫기  Esc', self.close)]:
            button = QPushButton(label)
            button.clicked.connect(action)
            self.bar.addWidget(button)
            self.buttons[label] = button
        layout.addLayout(self.bar)
        self.canvas = Canvas()
        self.canvas.zoomed.connect(lambda scale: self.zoom_label.setText(f'{scale*100:.0f}% · 표시 이미지'))
        layout.addWidget(self.canvas, 1)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.info)
        for key, action in [('Right', lambda:self.step(1)), ('Left', lambda:self.step(-1)), ('F11',self.fullscreen), ('Ctrl+0',self.canvas.fit), ('Ctrl+1',self.actual)]:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(action)
        self.show_current(actual=self.mode=='actual')
    def show_current(self, actual=False):
        self.generation += 1
        generation = self.generation
        if self.current_job:
            self.current_job.cancelled.set()
        row = self.rows[self.position]
        self.title.setText(f"{self.position+1} / {self.total}   {row['name']}")
        self.info.setText(f"불러오는 중…  ·  {row['width']:,} × {row['height']:,}\n{row['path']}")
        self.buttons['☆'].setText('★' if row['favorite'] else '☆')
        self.buttons['‹'].setEnabled(self.position>0)
        self.buttons['›'].setEnabled(self.position<self.total-1)
        # Avoid showing the previous photo under a new filename while decoding.
        self.canvas.scene().clear()
        self.canvas.item = None
        def done(image):
            if generation != self.generation:
                return
            self.canvas.show_image(image)
            if actual:
                self.canvas.resetTransform()
                ratio=self.canvas.devicePixelRatioF()
                self.canvas.scale(1/ratio,1/ratio)
                self.canvas.zoomed.emit(1.0)
                self.canvas.fit_mode = False
            limited = image.width()<row['width'] or image.height()<row['height']
            self.info.setText(f"{row['width']:,} × {row['height']:,}  ·  {row['format']}  ·  {'빠른 미리보기 — 원본 픽셀 버튼으로 전체 해상도 로드' if limited else '전체 해상도'}\n{row['path']}")
        def error(message):
            if generation == self.generation:
                self.info.setText('이미지를 열 수 없습니다. 갤러리에서 파일을 다시 연결하세요.\n'+message)
        self.current_job = self.tasks.submit(lambda _:decode_preview(row['path'], max(row['width'],row['height']) if actual else 4096), done, error)
    def actual(self):
        self.show_current(actual=True)
    def step(self, delta):
        value = self.position+delta
        if 0<=value<len(self.rows):
            self.position = value
            self.show_current(actual=self.mode=='actual')
        elif delta>0 and self.load_more and len(self.rows)<self.total and not self.fetching:
            self.fetching = True
            self.info.setText('다음 사진 목록을 불러오는 중…')
            offset = len(self.rows)
            def done(result):
                self.fetching = False
                extra, self.total = result
                self.rows.extend(extra)
                if extra:
                    self.position = offset
                    self.show_current(actual=self.mode=='actual')
            def error(message):
                self.fetching = False
                self.info.setText('목록을 불러오지 못했습니다: '+message)
            self.tasks.submit(lambda _: self.load_more(offset), done, error)
    def fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()
    def favorite(self):
        row = self.rows[self.position]
        row['favorite'] = not row['favorite']
        self.buttons['☆'].setText('★' if row['favorite'] else '☆')
        self.favorite_changed.emit(row['id'], bool(row['favorite']))
    def done(self, result):
        self.generation += 1
        self.tasks.stop()
        super().done(result)
    def closeEvent(self, event):
        self.generation += 1
        self.tasks.stop()
        super().closeEvent(event)
