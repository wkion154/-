"""On-demand relationship browsing, with the existing bounded thumbnail pipeline."""
import html
from PySide6.QtCore import Qt, QAbstractListModel, QModelIndex, QTimer
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,
                              QSplitter,QWidget,QInputDialog,QMessageBox,QCheckBox,QLineEdit,QScrollArea,QFrame)
from .gallery import GalleryView
from .related import RelatedRepository
from .workers import Tasks
from .viewer import Viewer


class RelatedModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent);self.rows=[]
    def rowCount(self,parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)
    def row_at(self,position,request=True):
        return self.rows[position] if 0<=position<len(self.rows) else None
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        row=self.row_at(index.row()) if index.isValid() else None
        if row and role==Qt.ItemDataRole.UserRole:return row
        if row and role==Qt.ItemDataRole.DisplayRole:return row['name']
        if row and role==Qt.ItemDataRole.ToolTipRole:return '<span>'+html.escape('\n'.join(row['reasons'])).replace('\n','<br>')+'</span>'
    def replace(self,rows):
        self.beginResetModel();self.rows=rows;self.endResetModel()


class LocalRightsDialog(QDialog):
    """An explicit local-only review, never a blanket grant for imported web files."""
    def __init__(self,parent=None):
        super().__init__(parent);self.setWindowTitle('선택 로컬 사진의 참조 사용 권한');self.resize(530,300)
        layout=QVBoxLayout(self)
        label=QLabel('직접 권리를 확인한 로컬 사진만 적용하세요.\n이 설정은 선택한 사진과 로컬 출처의 해당 작업을 허용합니다.\n다른 사진의 개별 허용 상태는 바뀌지 않습니다.')
        label.setWordWrap(True);layout.addWidget(label)
        self.analysis=QCheckBox('이 사진의 로컬 이미지 분석을 허용할 근거를 확인했습니다.')
        self.ai=QCheckBox('이 사진을 AI 복원 입력으로 사용할 근거를 확인했습니다.')
        layout.addWidget(self.analysis);layout.addWidget(self.ai)
        self.reference=QLineEdit();self.reference.setPlaceholderText('권리 문서/자체 촬영 기록 식별자')
        self.note=QLineEdit();self.note.setPlaceholderText('확인한 이용 범위와 근거 설명')
        layout.addWidget(self.reference);layout.addWidget(self.note)
        row=QHBoxLayout();cancel=QPushButton('취소');cancel.clicked.connect(self.reject);row.addWidget(cancel)
        save=QPushButton('선택 사진에 기록');save.clicked.connect(self.validate);row.addWidget(save);layout.addLayout(row)
    def validate(self):
        if not self.reference.text().strip() or not self.note.text().strip():
            QMessageBox.warning(self,'근거 확인','권리 기록 식별자와 검토 내용을 입력하세요.');return
        self.accept()


class RelatedDialog(QDialog):
    def __init__(self,db,settings,identity,parent=None):
        super().__init__(parent)
        self.db,self.settings,self.identity=db,settings,identity
        self.repo=RelatedRepository(db)
        self.closed=False;self.generation=0;self.job=None;self.people_ready=False
        self.saving=False;self.close_after_save=None;self.opening=False
        self.tasks=Tasks(self,1)
        self.setWindowTitle('연관 사진 · K-IDOL ARCHIVE');self.resize(1160,820)
        layout=QVBoxLayout(self)
        kicker=QLabel('CONNECTED MOMENTS');kicker.setObjectName('eyebrow');layout.addWidget(kicker)
        self.heading=QLabel('연관 사진');self.heading.setObjectName('heading');self.heading.setTextFormat(Qt.TextFormat.PlainText);self.heading.setWordWrap(True);layout.addWidget(self.heading)
        hint=QLabel('기록된 메타데이터와 사용자 확인을 기준으로 찾습니다. 같은 인물·날짜만으로 같은 촬영을 확정하지 않습니다.')
        hint.setWordWrap(True);hint.setObjectName('muted');layout.addWidget(hint)
        bar=QHBoxLayout()
        self.mode=QComboBox();self.mode.addItem('연관 사진','related');self.mode.addItem('권한 확인된 인물 참조 후보','reference');bar.addWidget(self.mode)
        self.person=QComboBox();self.person.addItem('인물 선택',None);self.person.setVisible(False);bar.addWidget(self.person)
        self.seed_rights=QPushButton('기준 사진 권한');self.seed_rights.clicked.connect(lambda:self.review_rights(self.identity));bar.addWidget(self.seed_rights)
        refresh=QPushButton('새로 찾기');refresh.clicked.connect(self.reload);bar.addWidget(refresh);bar.addStretch();layout.addLayout(bar)
        self.status=QLabel('근거를 확인하는 중…');self.status.setWordWrap(True);self.status.setObjectName('muted');layout.addWidget(self.status)
        split=QSplitter();layout.addWidget(split,1)
        self.model=RelatedModel(self);self.gallery=GalleryView(self.model,settings.cache,self)
        self.gallery.authorize=lambda row:self.repo.allowed(row['id'],('thumbnail',))
        self.gallery.set_size(184);split.addWidget(self.gallery)
        details=QWidget();details.setMinimumWidth(245);info=QVBoxLayout(details)
        self.name=QLabel('사진을 선택하세요');self.name.setTextFormat(Qt.TextFormat.PlainText);self.name.setWordWrap(True);info.addWidget(self.name)
        self.evidence=QLabel('찾은 관계의 근거가 여기에 표시됩니다.');self.evidence.setTextFormat(Qt.TextFormat.PlainText);self.evidence.setWordWrap(True);info.addWidget(self.evidence)
        self.actions=[]
        for label,fn in [('뷰어에서 열기',self.open_selected),('동일 행사로 확인',lambda:self.confirm('confirmed_event')),('연관 사진으로 연결',lambda:self.confirm('related')),('이 관계 제외',lambda:self.confirm('excluded')),('수동 관계 해제',self.clear),('선택 사진 권한',self.review_selected)]:
            button=QPushButton(label);button.clicked.connect(fn);info.addWidget(button);self.actions.append(button)
        info.addStretch();note=QLabel('참조 후보는 복원 품질을 보장하지 않습니다.\n얼굴 크기·각도·선명도 및 동일 촬영은 미평가입니다.\nAI 모델 실행과 파일 전송은 수행하지 않습니다.');note.setWordWrap(True);note.setObjectName('muted');info.addWidget(note)
        detail_scroll=QScrollArea();detail_scroll.setWidgetResizable(True);detail_scroll.setFrameShape(QFrame.Shape.NoFrame);detail_scroll.setWidget(details)
        split.addWidget(detail_scroll);split.setSizes([840,280])
        close=QPushButton('닫기');close.clicked.connect(self.accept);layout.addWidget(close)
        self.gallery.selectionModel().currentChanged.connect(self.selection_changed)
        self.gallery.doubleClicked.connect(lambda _:self.open_selected())
        self.mode.currentIndexChanged.connect(self.reload);self.person.currentIndexChanged.connect(self.reload)
        self.set_actions(False)
        QTimer.singleShot(0,self.reload)

    def set_actions(self,enabled):
        for button in self.actions:button.setEnabled(enabled and not self.saving)
    def selected(self):
        index=self.gallery.currentIndex()
        return self.model.row_at(index.row()) if index.isValid() else None
    def reload(self,*_):
        if self.closed or self.saving:return
        self.generation+=1;generation=self.generation
        if self.job:self.job.cancelled.set()
        self.model.replace([]);self.set_actions(False)
        self.name.setText('사진을 선택하세요');self.evidence.setText('찾은 관계의 근거가 여기에 표시됩니다.')
        reference=self.mode.currentData()=='reference';self.person.setVisible(reference)
        person=self.person.currentData()
        self.status.setText('기록된 근거를 찾는 중…')
        def done(result):
            if self.closed or generation!=self.generation:return
            self.heading.setText(result['seed']['name'])
            self.seed_rights.setEnabled(self.repo.is_local(self.identity))
            if not self.people_ready:
                self.people_ready=True;self.person.blockSignals(True)
                for pid,name in result['people'].items():self.person.addItem(name,pid)
                if len(result['people'])==1:self.person.setCurrentIndex(1)
                self.person.blockSignals(False)
            self.model.replace(result['rows'])
            self.status.setText(f"{len(result['rows'])}개 후보 · 관련도는 메타데이터 규칙 점수"+(' · 검색 범위 제한됨' if result['truncated'] else '')+(f" · 권한 미확인 {result['blocked']}개 제외" if result['blocked'] else '')+(' · '+result['message'] if result['message'] else ''))
            if result['rows']:self.gallery.setCurrentIndex(self.model.index(0))
        def error(message):
            if not self.closed and generation==self.generation:self.status.setText('찾지 못했습니다 · '+message)
        self.job=self.tasks.submit(lambda cancelled:self.repo.search(self.identity,cancelled,reference,person),done,error)

    def selection_changed(self,index,previous=None):
        row=self.selected();self.set_actions(row is not None)
        if not row:return
        self.name.setText(row['name'])
        self.evidence.setText(f"{row['width']:,} × {row['height']:,}\n규칙 점수 {row['score']} · 확률이 아닙니다.\n\n"+'\n\n'.join(row['reasons']))
        origins=row.get('origins',[])
        if origins:self.evidence.setText(self.evidence.text()+'\n\n출처 기록\n'+'\n'.join(x['name']+('\n'+x['page_url'] if x['page_url'] else '') for x in origins[:3]))
        self.actions[-1].setEnabled(self.repo.is_local(row['id']))

    def open_selected(self):
        row=self.selected()
        if not row:return
        # Revalidate rights immediately before using this alternate viewing path.
        if not self.repo.allowed(row['id'],('thumbnail',)):
            self.status.setText('이 사진의 표시 권한이 변경됐습니다.');self.reload();return
        if not self.repo.is_local(row['id']) and not self.repo.allowed(row['id'],('original_download',)):
            self.status.setText('원본 보기 권한을 먼저 확인하세요.');return
        if self.opening:return
        self.opening=True;generation=self.generation
        selected_id=row['id'];snapshot=[dict(item) for item in self.model.rows]
        self.status.setText('표시 가능한 원본 목록을 준비하는 중…')
        def prepare(cancelled):
            rows=[]
            for item in snapshot:
                if cancelled.is_set():return []
                identity=item['id']
                if self.repo.allowed(identity,('thumbnail',)) and (self.repo.is_local(identity) or self.repo.allowed(identity,('original_download',))):rows.append(item)
            return rows
        def show(rows):
            self.opening=False
            if self.closed or generation!=self.generation:return
            position=next((i for i,r in enumerate(rows) if r['id']==selected_id),None)
            if position is None:
                self.status.setText('표시 권한이 변경됐습니다. 새로 찾아 주세요.');return
            viewer=Viewer(rows,position,self,mode=self.settings.values['viewer_mode'],related_repo=self.repo,related_cache=self.settings.cache)
            def favorite(identity,value):
                def done(_):
                    for item in self.model.rows:
                        if item['id']==identity:item['favorite']=value
                    self.gallery.viewport().update()
                self.tasks.submit(lambda _:self.db.favorite(identity,value),done,lambda message:self.status.setText(message))
            viewer.favorite_changed.connect(favorite)
            def recenter(identity):
                viewer.accept();self.identity=identity;self.people_ready=False
                self.person.blockSignals(True);self.person.clear();self.person.addItem('인물 선택',None);self.person.blockSignals(False)
                self.reload()
            viewer.related_requested.connect(recenter)
            viewer.exec();viewer.deleteLater()
            if generation==self.generation:self.status.setText(f'{len(self.model.rows)}개 연관 후보 · 원본은 변경하지 않습니다.')
        def error(message):
            self.opening=False
            if not self.closed:self.status.setText('뷰어를 열지 못했습니다 · '+message)
        self.tasks.submit(prepare,show,error)

    def confirm(self,kind):
        row=self.selected()
        if not row:return
        text,ok=QInputDialog.getText(self,'관계 근거 기록','같은 행사 확인 근거 또는 연결/제외 이유')
        if ok:
            identity=self.identity;other=row['id']
            self.mutate(lambda:self.repo.override(identity,other,kind,text))
    def clear(self):
        row=self.selected()
        if row:
            identity=self.identity;other=row['id']
            self.mutate(lambda:self.repo.clear_override(identity,other))
    def review_selected(self):
        row=self.selected()
        if row:self.review_rights(row['id'])
    def review_rights(self,identity):
        if not self.repo.is_local(identity):
            self.status.setText('원격 출처 권한은 온라인 → 출처 · URL에서 확인하세요.');return
        dialog=LocalRightsDialog(self)
        current=self.repo.sources.permissions('local','asset:'+identity)
        dialog.analysis.setChecked(current['local_analysis']['state']=='allowed')
        dialog.ai.setChecked(current['ai_restore_input']['state']=='allowed')
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        values={'local_analysis':'allowed' if dialog.analysis.isChecked() else 'denied','ai_restore_input':'allowed' if dialog.ai.isChecked() else 'denied'}
        evidence={'reference':dialog.reference.text().strip(),'note':dialog.note.text().strip()}
        def save():
            self.repo.sources.grant_many('local',{op:state for op,state in values.items() if state=='allowed'},evidence,enabled=True)
            self.repo.sources.grant_many('local',values,evidence,resource='asset:'+identity)
        self.mutate(save)
    def mutate(self,operation):
        if self.saving:return
        self.saving=True;self.generation+=1;self.set_actions(False)
        if self.job:self.job.cancelled.set()
        self.mode.setEnabled(False);self.person.setEnabled(False);self.seed_rights.setEnabled(False)
        self.status.setText('확인 내용을 기록하는 중…')
        def finish(message=None):
            self.saving=False;self.mode.setEnabled(True);self.person.setEnabled(True)
            if message:
                self.close_after_save=None;self.reload()
                QMessageBox.warning(self,'기록하지 못했습니다',message);return
            if self.close_after_save is not None:
                result=self.close_after_save;self.close_after_save=None;self.done(result);return
            self.reload()
        self.tasks.submit(lambda _:operation(),lambda _:finish(),finish)

    def done(self,result):
        if self.saving:
            self.close_after_save=result;self.status.setText('기록을 마친 뒤 닫습니다.');return
        if not self.closed:
            self.closed=True;self.generation+=1;self.gallery.close_tasks();self.tasks.stop()
        super().done(result)
    def closeEvent(self,event):
        self.done(QDialog.DialogCode.Rejected)
        if self.closed:event.accept()
        else:event.ignore()
