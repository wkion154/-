"""Modeless restoration workspace and original-coordinate comparison canvas."""
import threading
from PySide6.QtCore import Qt,QRectF,Signal,QPointF,QTimer
from PySide6.QtGui import QPainter,QColor,QImage,QKeySequence,QShortcut
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QProgressBar,QSlider,QWidget,QCheckBox,QLineEdit,QFormLayout,QDialogButtonBox)
from .workers import Tasks
from .images import decode_preview
from .super_resolution import available,diagnostics
from .face_reference import available as face_available
from .providers.http import Cancelled


class CompareCanvas(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.images=[None,None,None];self.original_size=(1,1)
        self.center=QPointF(.5,.5);self.zoom=None;self.mode='triple';self.split=.5;self.drag=None
        self.setMinimumSize(480,260);self.setMouseTracking(True)
    def set_images(self,images,size):
        self.images=images;self.original_size=size;self.center=QPointF(.5,.5);self.zoom=None;self.update()
    def panes(self):return 3 if self.mode=='triple' else 2 if self.mode=='pair' else 1
    def scale(self):
        w,h=self.original_size
        return self.zoom if self.zoom is not None else min((self.width()/self.panes()-20)/w,(self.height()-50)/h)
    def image_rect(self,index):
        width=self.width()/self.panes();w,h=self.original_size;s=self.scale()
        return QRectF(index*width+width/2-self.center.x()*w*s,self.height()/2+12-self.center.y()*h*s,w*s,h*s)
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#101115'));painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        labels=['원본','일반 · FSRCNN','참조 보조 · 실험']
        def draw(index,pane):
            im=self.images[index]
            if im is not None:painter.drawImage(self.image_rect(pane),im)
            else:
                painter.setPen(QColor('#969baa'));painter.drawText(QRectF(pane*self.width()/self.panes(),60,self.width()/self.panes(),self.height()-100),Qt.AlignmentFlag.AlignCenter,'결과 없음 · 실행 후 비교')
        if self.mode=='swipe':
            split=self.width()*self.split
            painter.save();painter.setClipRect(QRectF(0,32,split,self.height()-32));draw(0,0);painter.restore()
            painter.save();painter.setClipRect(QRectF(split,32,self.width()-split,self.height()-32));draw(2 if self.images[2] is not None else 1,0);painter.restore()
            painter.setPen(QColor('#c1b6ff'));painter.drawLine(int(split),32,int(split),self.height())
            painter.drawText(12,22,'원본 / 결과 · 슬라이더 비교')
        else:
            for index in range(self.panes()):
                width=self.width()/self.panes();painter.save();painter.setClipRect(QRectF(index*width,32,width,self.height()-32));draw(index,index);painter.restore()
                painter.fillRect(QRectF(index*width,0,width,32),QColor('#1c1d23'));painter.setPen(QColor('#d8d5e8'));painter.drawText(int(index*width+12),22,labels[index]);painter.drawLine(int(index*width),32,int(index*width),self.height())
    def wheelEvent(self,event):
        old=self.scale();new=max(.005,min(32,old*(1.2 if event.angleDelta().y()>0 else 1/1.2)))
        pane_width=self.width()/self.panes();point=event.position();px=point.x()%pane_width-pane_width/2;py=point.y()-self.height()/2-12
        w,h=self.original_size
        self.center+=QPointF(px/w*(1/old-1/new),py/h*(1/old-1/new));self.zoom=new;self.update();event.accept()
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.drag=event.position();self.setCursor(Qt.CursorShape.ClosedHandCursor)
    def mouseMoveEvent(self,event):
        if self.drag is not None:
            delta=event.position()-self.drag;w,h=self.original_size;s=self.scale()
            self.center-=QPointF(delta.x()/(w*s),delta.y()/(h*s));self.drag=event.position();self.update()
    def mouseReleaseEvent(self,event):self.drag=None;self.unsetCursor()
    def fit(self):self.zoom=None;self.center=QPointF(.5,.5);self.update()
    def actual(self):self.zoom=1/self.devicePixelRatioF();self.update()


class UpscaleDialog(QDialog):
    progress=Signal(float,str)
    def __init__(self,service,identity,parent=None):
        super().__init__(parent);self.service=service;self.identity=identity;self.tasks=Tasks(self,1);self.job=None;self.busy=False;self.results={};self.closed=False;self.cpu_ready=False
        self.setWindowTitle('AI 업스케일 · 원본 좌표 비교');self.resize(1320,850)
        layout=QVBoxLayout(self);title=QLabel('RESTORE / COMPARE');title.setObjectName('eyebrow');layout.addWidget(title)
        controls=QHBoxLayout();layout.addLayout(controls)
        self.scale=QComboBox();self.scale.addItem('2배 · FSRCNN',2);self.scale.addItem('4배 · FSRCNN',4);controls.addWidget(self.scale)
        self.backend=QComboBox();self.backend.addItem('CPU','cpu');self.backend.addItem('CUDA · 진단 필요','cuda');self.backend.model().item(1).setEnabled(False);controls.addWidget(self.backend)
        self.person=QComboBox();self.person.addItem('확인된 인물 불러오는 중',None);controls.addWidget(self.person)
        self.general=QPushButton('일반 업스케일');self.general.clicked.connect(lambda:self.run('general'));controls.addWidget(self.general)
        self.reference=QPushButton('참조 자동 선별 · 실험');self.reference.clicked.connect(lambda:self.run('reference'));controls.addWidget(self.reference)
        self.rights=QPushButton('선택 사진 권한');self.rights.clicked.connect(self.review_rights);controls.addWidget(self.rights)
        self.force=QCheckBox('재생성');controls.addWidget(self.force)
        self.cancel=QPushButton('취소');self.cancel.clicked.connect(self.cancel_job);self.cancel.setEnabled(False);controls.addWidget(self.cancel)
        self.canvas=CompareCanvas();layout.addWidget(self.canvas,1)
        bar=QHBoxLayout();layout.addLayout(bar)
        mode=QComboBox();self.compare_mode=mode
        for label,value in [('3분할','triple'),('원본 / 일반','pair'),('원본 / 결과 슬라이더','swipe')]:mode.addItem(label,value)
        mode.currentIndexChanged.connect(lambda:self.set_mode(mode.currentData()));bar.addWidget(mode)
        fit=QPushButton('맞춤');fit.clicked.connect(self.canvas.fit);bar.addWidget(fit)
        actual=QPushButton('원본 100%');actual.clicked.connect(self.canvas.actual);bar.addWidget(actual)
        full=QPushButton('전체화면');full.clicked.connect(lambda:self.showNormal() if self.isFullScreen() else self.showFullScreen());bar.addWidget(full)
        split=QSlider(Qt.Orientation.Horizontal);split.setRange(0,100);split.setValue(50);split.valueChanged.connect(lambda v:self.set_split(v/100));bar.addWidget(split,1)
        exports=QHBoxLayout();layout.addLayout(exports);exports.addStretch()
        self.save_original=QPushButton('원본 별도 저장');self.save_original.clicked.connect(self.export_original);exports.addWidget(self.save_original)
        self.save_general=QPushButton('일반 결과 보관');self.save_general.clicked.connect(lambda:self.export('general'));exports.addWidget(self.save_general)
        self.save_reference=QPushButton('참조 결과 보관');self.save_reference.clicked.connect(lambda:self.export('reference'));exports.addWidget(self.save_reference)
        delete=QPushButton('표시 결과 캐시 비우기');delete.clicked.connect(self.delete_results);exports.addWidget(delete)
        self.note=QLabel('추정 디테일은 실제 복구 사실이 아닙니다. 참조는 확인된 같은 인물의 얼굴 방향·눈/입 상태·선명도를 검사하고 얼굴 내부만 제한적으로 보조합니다.\n부적합한 표정·각도는 보류하며 정체성 보존 점수는 신원 증명이 아닙니다. 결과는 원본과 별도로 저장합니다.');self.note.setWordWrap(True);self.note.setObjectName('muted');layout.addWidget(self.note)
        self.status=QLabel('선택 의존성 '+('설치됨 · CPU 처리' if available() else '미설치 · requirements-upscale.lock 설치 필요')+(' · 참조 모델 사용 가능' if face_available() else ' · 참조: requirements-reference.lock 필요'));self.status.setWordWrap(True);layout.addWidget(self.status)
        self.progress.connect(lambda _,message:self.status.setText(message))
        QShortcut(QKeySequence('Escape'),self,activated=lambda:self.showNormal() if self.isFullScreen() else self.close())
        self.ready=False;self.update_controls()
        self.tasks.submit(lambda cancelled:self.load(cancelled),self.loaded,self.fail)
    def set_mode(self,value):self.canvas.mode=value;self.canvas.update()
    def set_split(self,value):self.canvas.split=value;self.canvas.update()
    def load(self,cancelled):
        row=self.service.related.asset(self.identity)
        # Viewing local originals is separate from AI use. Actual inference has strict gates.
        if not self.service.related.allowed(self.identity,('thumbnail',)):raise ValueError('원본 표시 권한을 확인하세요.')
        image=decode_preview(row['path'],2048)
        results={};images=[image,None,None]
        for result in self.service.recent(self.identity):
            mode=result['config']['mode']
            if mode in results:continue
            try:
                self.service.authorized_result(self.identity,result['id'],cancelled)
                preview=decode_preview(result['path'],2048)
            except (ValueError,OSError):continue
            results[mode]=result;images[1 if mode=='general' else 2]=preview
        return dict(diagnostics=diagnostics(),row=row,people=self.service.people(self.identity),images=images,results=results)
    def loaded(self,value):
        self.results=value['results'];self.person.clear();self.cpu_ready=value['diagnostics']['cpu_ready']
        self.backend.model().item(1).setEnabled(value['diagnostics']['cuda_ready'])
        self.backend.setItemText(1,'CUDA' if value['diagnostics']['cuda_ready'] else 'CUDA 사용 불가')
        self.status.setText(value['diagnostics']['reason']+(' · 참조 모델 설치됨' if face_available() else ' · 참조: requirements-reference.lock 설치 필요'))
        for identity,name in value['people'].items():self.person.addItem(name,identity)
        if not self.person.count():self.person.addItem('인물 연결 미확정',None)
        self.canvas.set_images(value['images'],(value['row']['width'],value['row']['height']));self.ready=True;self.update_controls()
    def update_controls(self):
        for button in (self.general,self.reference,self.rights,self.scale,self.backend,self.person,self.force,self.save_original):button.setEnabled(self.ready and not self.busy)
        self.general.setEnabled(self.ready and not self.busy and available() and self.cpu_ready);self.reference.setEnabled(self.general.isEnabled() and face_available() and self.person.currentData() is not None)
        self.cancel.setEnabled(self.busy and self.job is not None)
        self.save_general.setEnabled(not self.busy and 'general' in self.results);self.save_reference.setEnabled(not self.busy and 'reference' in self.results)
    def run(self,mode):
        if self.busy:return
        scale=self.scale.currentData();backend=self.backend.currentData();person=self.person.currentData();force=self.force.isChecked();self.busy=True;self.cancel_request=threading.Event()
        self.status.setText('권한과 입력 검증 중…')
        def operation(cancelled):
            try:
                result=self.service.run(self.identity,self.cancel_request,scale,mode,person,force,self.progress.emit,backend)
                return result,decode_preview(result['path'],2048)
            except Cancelled:return None
        def done(value):
            self.busy=False;self.job=None
            if value is None:self.status.setText('작업을 취소했습니다.');self.update_controls();return
            result,image=value;self.results[mode]=result;self.canvas.images[1 if mode=='general' else 2]=image;self.canvas.update()
            self.status.setText(('기존 결과 재사용' if result.get('reused') else '완료')+f" · {result['metrics']['width']} × {result['metrics']['height']} · {result['metrics']['elapsed_seconds']:.2f}초 · {result['metrics']['device']} · 추정 디테일")
            if result['metrics'].get('cuda_fallback_reason'):self.status.setText(self.status.text()+' · CUDA 실패 후 CPU 재시도')
            self.update_controls()
        self.job=self.tasks.submit(operation,done,self.fail);self.update_controls()
    def fail(self,message):self.busy=False;self.job=None;self.status.setText(message);self.update_controls()
    def cancel_job(self):
        if self.job:
            self.cancel_request.set();self.cancel.setEnabled(False);self.status.setText('취소 요청 · 실행 프로세스를 정리하는 중…')
    def review_rights(self):
        if not self.service.related.is_local(self.identity):self.status.setText('원격 출처는 온라인 → 출처 · URL에서 출처/사진별 권한을 확인하세요.');return
        dialog=QDialog(self);dialog.setWindowTitle('선택 사진의 복원 권한');form=QFormLayout(dialog);checks={}
        current=self.service.sources.permissions('local','asset:'+self.identity)
        for op,label in [('local_analysis','로컬 분석'),('ai_restore_input','AI 복원 입력'),('result_storage','결과 저장·이용')]:
            box=QCheckBox(label);box.setChecked(current[op]['state']=='allowed');checks[op]=box;form.addRow(box)
        reference=QLineEdit();note=QLineEdit();form.addRow('근거 문서/식별자',reference);form.addRow('확인한 이용 범위',note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        if not reference.text().strip() or not note.text().strip():self.status.setText('근거 문서와 확인 내용을 입력하세요.');return
        values={op:'allowed' if box.isChecked() else 'denied' for op,box in checks.items()};evidence=dict(reference=reference.text().strip(),note=note.text().strip())
        def save(_):
            # Checking this photo records source scope as well, but never other photos.
            self.service.sources.grant_many('local',{op:'allowed' for op,value in values.items() if value=='allowed'},evidence,enabled=True)
            self.service.sources.grant_many('local',values,evidence,'asset:'+self.identity)
        self.busy=True;self.update_controls()
        def done(_):self.busy=False;self.status.setText('선택 사진의 권한을 저장했습니다.');self.update_controls()
        self.tasks.submit(save,done,self.fail)
    def export(self,mode):
        if self.busy or mode not in self.results:return
        result=self.results[mode];self.busy=True;self.update_controls()
        def done(path):self.busy=False;self.status.setText('영구 보관 완료 · '+path);self.update_controls()
        self.tasks.submit(lambda cancel:self.service.export(self.identity,result['id'],cancel),done,self.fail)
    def export_original(self):
        if self.busy:return
        self.busy=True;self.update_controls()
        def done(path):self.busy=False;self.status.setText('원본 별도 저장 · '+path);self.update_controls()
        self.tasks.submit(lambda cancel:self.service.export_original(self.identity,cancel),done,self.fail)
    def delete_results(self):
        if self.busy:return
        results=list(self.results.values());self.busy=True;self.update_controls()
        def clear(_):
            for result in results:self.service.delete_cache(self.identity,result['id'])
        def done(_):self.busy=False;self.results={};self.canvas.images[1:]=[None,None];self.canvas.update();self.status.setText('표시 결과 캐시를 비웠습니다. 영구 보관본은 유지합니다.');self.update_controls()
        self.tasks.submit(clear,done,self.fail)
    def closeEvent(self,event):
        if self.busy:
            self.cancel_job();event.ignore();QTimer.singleShot(100,self.close);return
        self.closed=True;self.tasks.stop();event.accept()
