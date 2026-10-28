from collections import OrderedDict
from pathlib import Path
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QSize, QTimer, Signal, QRect, QPoint
from PySide6.QtGui import QColor, QPainter, QPixmap, QFont
from PySide6.QtWidgets import QListView, QStyledItemDelegate, QStyle
from .images import make_thumbnail
from .workers import Tasks

class GalleryModel(QAbstractListModel):
    changed = Signal(int)
    failed = Signal(str)
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.rows = []
        self.total = 0
        self.scope = None
        self.search = ''
        self.sort = 'newest'
        self.generation = 0
        self.loading = False
        self.tasks = Tasks(self, 1)
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row['name']
        if role == Qt.ItemDataRole.UserRole:
            return row
        if role == Qt.ItemDataRole.ToolTipRole:
            return row['name']+'\n'+row['path']
    def reload(self, scope=None, search='', sort='newest'):
        self.generation += 1
        self.scope, self.search, self.sort = scope, search, sort
        self.beginResetModel()
        self.rows = []
        self.total = 0
        self.endResetModel()
        self.loading = False
        self.fetchMore()
    def canFetchMore(self, parent=QModelIndex()):
        return not parent.isValid() and not self.loading and len(self.rows) < self.total
    def fetchMore(self, parent=QModelIndex()):
        if self.loading:
            return
        self.loading = True
        generation = self.generation
        scope, search, sort, offset = self.scope, self.search, self.sort, len(self.rows)
        def done(result):
            if generation != self.generation:
                return
            rows, self.total = result
            if rows:
                self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows)+len(rows)-1)
                self.rows.extend(rows)
                self.endInsertRows()
            self.loading = False
            self.changed.emit(self.total)
        def error(message):
            if generation == self.generation:
                self.loading = False
                self.failed.emit(message)
        self.tasks.submit(lambda _: self.db.query(scope, search, sort, offset), done, error)

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
        self.pending = set()
        self.cache = cache
        self.tasks = Tasks(self, 2)
        self.edge = 216
        self.setItemDelegate(PhotoDelegate(self))
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.load_visible)
        self.verticalScrollBar().valueChanged.connect(self.schedule)
        model.rowsInserted.connect(self.schedule)
        model.modelReset.connect(self.reset_cache)
        self.set_size(216)
    def reset_cache(self):
        self.thumbs.clear()
        self.errors.clear()
        self.schedule()
    def set_size(self, edge):
        self.edge = edge
        self.setGridSize(QSize(edge+20, edge+62))
        self.schedule()
    def schedule(self, *_):
        self.timer.start(35)
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
        for rownum in range(start, min(model.rowCount(), start+count)):
            row = model.rows[rownum]
            key = row['sha256']
            if key in self.thumbs or key in self.pending or key in self.errors:
                continue
            if len(self.pending) >= 8:
                break
            self.pending.add(key)
            path, cache = row['path'], self.cache
            def done(filename, key=key):
                self.pending.discard(key)
                pix = QPixmap(filename)
                if not pix.isNull():
                    self.thumbs[key] = pix
                    # 96 * 512 * 512 * 4 = at most approximately 96 MiB of decoded thumbnails.
                    while len(self.thumbs) > 96:
                        self.thumbs.popitem(last=False)
                self.viewport().update()
                self.schedule()
            def error(message, key=key):
                self.pending.discard(key)
                self.errors.add(key)
                self.viewport().update()
                self.schedule()
            self.tasks.submit(lambda _, path=path, cache=cache, key=key: make_thumbnail(path, cache, key), done, error)
    def close_tasks(self):
        self.timer.stop()
        self.tasks.stop()

class PhotoDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole)
        view = self.parent()
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
