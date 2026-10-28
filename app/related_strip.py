"""Small opt-in filmstrip: twelve items, one worker, no downloads."""
import html
from PySide6.QtCore import Qt,QSize,Signal,QTimer,QThread
from PySide6.QtGui import QIcon,QPixmap
from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel,QListWidget,QListWidgetItem,QListView
from .workers import Tasks
from .images import make_thumbnail,decode_preview


class RelatedStrip(QWidget):
    activated=Signal(str)
    def __init__(self,repo,cache,parent=None):
        super().__init__(parent)
        self.repo,self.cache=repo,cache
        self.seed=None;self.generation=0;self.closed=False;self.job=None
        self.tasks=Tasks(self,1)
        self.tasks.pool.setThreadPriority(QThread.Priority.LowPriority)
        self.rows=[];self.processed=0;self.loaded=0
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(180);self.timer.timeout.connect(self.search)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.label=QLabel('연관 사진 미리보기 · 더블클릭으로 근거와 목록 열기');self.label.setObjectName('muted');layout.addWidget(self.label)
        self.list=QListWidget();self.list.setViewMode(QListView.ViewMode.IconMode);self.list.setFlow(QListView.Flow.LeftToRight)
        self.list.setWrapping(False);self.list.setMovement(QListView.Movement.Static);self.list.setIconSize(QSize(96,96));self.list.setGridSize(QSize(122,120))
        self.list.setFixedHeight(140);self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);layout.addWidget(self.list)
        self.list.itemDoubleClicked.connect(self.activate)
        self.hide()
    def activate(self,item):
        identity=item.data(Qt.ItemDataRole.UserRole)
        if identity and self.repo.allowed(identity,('thumbnail',)):self.activated.emit(identity)
    def set_seed(self,identity):
        self.seed=identity
        if not self.isHidden():self.refresh()
    def toggle(self):
        if self.isHidden():self.show();self.refresh()
        else:self.hide();self.cancel();self.list.clear();self.rows=[]
    def cancel(self):
        self.generation+=1;self.timer.stop()
        if self.job:self.job.cancelled.set()
    def refresh(self):
        self.cancel();self.list.clear();self.label.setText('연관 사진을 찾는 중…');self.timer.start()
    def search(self):
        if self.closed or self.isHidden() or not self.seed:return
        generation=self.generation;seed=self.seed
        def done(result):
            if self.closed or generation!=self.generation or self.isHidden():return
            self.rows=result['rows'][:12];self.processed=0;self.loaded=0
            self.label.setText('연관 사진 미리보기 · 더블클릭으로 근거와 목록 열기' if self.rows else '표시할 연관 사진 없음 · 인물 연결·출처 권한을 확인하세요.')
            for row in self.rows:
                item=QListWidgetItem(row['name']);item.setData(Qt.ItemDataRole.UserRole,row['id'])
                item.setToolTip('<span>'+html.escape('\n'.join(row['reasons'])).replace('\n','<br>')+'</span>');self.list.addItem(item)
            self.load_next(generation)
        def error(message):
            if not self.closed and generation==self.generation:self.label.setText('연관 미리보기 실패 · '+message)
        self.job=self.tasks.submit(lambda cancelled:self.repo.search(seed,cancelled),done,error)

    def load_next(self,generation):
        if self.closed or generation!=self.generation or self.isHidden() or self.processed>=len(self.rows):return
        position=self.processed;row=self.rows[position]
        def work(cancelled):
            if not self.repo.allowed(row['id'],('thumbnail',)):raise ValueError('표시 권한 확인 필요')
            return decode_preview(make_thumbnail(row['path'],self.cache,row['sha256']),128)
        def done(image):
            if self.closed or generation!=self.generation or self.isHidden():return
            if self.repo.allowed(row['id'],('thumbnail',)):
                self.list.item(position).setIcon(QIcon(QPixmap.fromImage(image)));self.loaded+=1
            self.processed+=1;self.load_next(generation)
        def error(message):
            if self.closed or generation!=self.generation or self.isHidden():return
            self.list.item(position).setToolTip('파일 또는 표시 권한 확인 필요')
            self.processed+=1;self.load_next(generation)
        self.job=self.tasks.submit(work,done,error)

    def shutdown(self):
        if self.closed:return
        self.closed=True;self.cancel();self.tasks.stop();self.list.clear();self.rows=[]
