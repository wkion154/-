"""Dark source browser: proposals, watched artists, remote metadata and job status."""
import json
from PySide6.QtCore import Qt,QTimer,QUrl,Signal
from PySide6.QtGui import QPixmap,QDesktopServices
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTabWidget,QTableWidget,QTableWidgetItem,QHeaderView,QComboBox,QLineEdit,QFormLayout,QDialogButtonBox,QCheckBox,QSplitter,QWidget,QMessageBox)
from .workers import Tasks
from .providers.wikimedia import qid

KIND_LABELS={'discover':'신규 아티스트 탐색','enrich':'아티스트 정보 확인','refresh':'대상 정보 갱신','photos':'사진 목록 갱신','download':'원본 보관'}

def job_summary(job):
    if job['message']:return job['message']
    try:result=json.loads(job['result'] or '{}')
    except ValueError:return ''
    if 'asset_id' in result:return '원본 보관 완료 · 기존 파일 재사용' if result.get('reused') else '원본 보관 완료'
    if 'candidates' in result:return f"신규 후보 {result['candidates']}건 확인"
    if 'indexed' in result:return f"사진 {result['indexed']}건 목록 갱신"
    if 'registered' in result:return f"정보 {result.get('updated',0)}건 · 자동 등록 {result['registered']}건"
    if result.get('metadata_updated'):return f"대상 정보 갱신 · 소속 후보 {result.get('member_proposals',0)}명"
    return ''

STATES={'queued':'대기','running':'실행 중','retry':'재시도 대기','done':'완료','failed':'실패','blocked':'연결/정책 확인 필요','cancelled':'취소'}

class OnlineDialog(QDialog):
    library_changed=Signal()
    def __init__(self,service,runner,settings,parent=None):
        super().__init__(parent)
        self.service,self.repo,self.runner,self.settings=service,service.repo,runner,settings
        self.setWindowTitle('온라인 아카이브 · K-IDOL ARCHIVE')
        self.resize(1180,800)
        self.setMinimumSize(820,600)
        self.tasks=Tasks(self,1)
        self.preview_tasks=Tasks(self,1)
        self.preview_job=None
        self.generation=0
        self.closed=False
        self.snapshot_pending=False
        self.reload_requested=False
        self.selected_remote=None
        self.candidate_rows=[];self.target_rows=[];self.image_rows=[];self.job_rows=[]
        layout=QVBoxLayout(self)
        title=QLabel('ONLINE ARCHIVE');title.setObjectName('eyebrow');layout.addWidget(title)
        self.status=QLabel('저장된 출처 정보 확인 중…');self.status.setWordWrap(True);layout.addWidget(self.status)
        actions=QHBoxLayout()
        actions.addWidget(self.button('신규 아티스트 탐색',self.discover))
        actions.addWidget(self.button('Q ID로 대상 추가',self.add_qid))
        actions.addWidget(self.button('선택 대상 갱신',self.refresh_target))
        actions.addStretch()
        self.automatic=QCheckBox('실행 중 주기적 갱신')
        self.automatic.setChecked(settings.values.get('online_enabled',True))
        self.automatic.toggled.connect(self.automatic_changed)
        actions.addWidget(self.automatic)
        actions.addWidget(self.button('작업 중단',runner.pause))
        layout.addLayout(actions)
        self.auto_register=QCheckBox('근거가 충분한 신규 아티스트 자동 등록 · 불확실하거나 이름이 겹치면 검토 대기')
        self.auto_register.setChecked(settings.values.get('auto_register',True))
        self.auto_register.toggled.connect(self.auto_register_changed)
        layout.addWidget(self.auto_register)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        self.candidates=self.table(['이름','유형','외부 ID','상태','검토 이유'])
        self.targets=self.table(['이름','유형','외부 ID','Commons 카테고리'])
        self.jobs=self.table(['종류','상태','시도','결과/오류'])
        self.tabs.addTab(self.candidates,'등록 검토')
        self.tabs.addTab(self.targets,'갱신 대상')
        page=QWidget();body=QVBoxLayout(page)
        tools=QHBoxLayout()
        self.filter=QComboBox();self.filter.addItem('모든 등록 대상',None)
        self.filter.currentIndexChanged.connect(self.filter_changed)
        tools.addWidget(self.filter,1)
        self.offset=0
        tools.addWidget(self.button('이전 200개',lambda:self.page(-200)))
        tools.addWidget(self.button('다음 200개',lambda:self.page(200)))
        body.addLayout(tools)
        split=QSplitter();body.addWidget(split,1)
        self.images=self.table(['사진','해상도','이용 조건','원본 보관'])
        split.addWidget(self.images)
        details=QWidget();right=QVBoxLayout(details)
        self.preview=QLabel('사진을 선택하세요\n썸네일만 필요할 때 받습니다.')
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setMinimumSize(260,220)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(self.preview)
        self.details=QLabel('등장 인물은 자동 확정하지 않습니다.')
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.details,1)
        self.person_confirmed=QCheckBox('선택 인물이 실제 등장하는 사진임을 확인')
        right.addWidget(self.person_confirmed)
        self.download=self.button('선택 원본 보관',self.download_original)
        self.download.setEnabled(False);right.addWidget(self.download)
        self.source=self.button('출처 페이지 열기',self.open_source)
        self.source.setEnabled(False);right.addWidget(self.source)
        split.addWidget(details);split.setSizes([700,360])
        self.tabs.addTab(page,'온라인 사진')
        self.tabs.addTab(self.jobs,'작업 상태')
        bottom=QHBoxLayout()
        bottom.addWidget(self.button('선택 후보 등록/연결',self.accept))
        bottom.addWidget(self.button('선택 작업 재시도',self.retry))
        bottom.addStretch()
        bottom.addWidget(self.button('새로고침',self.reload))
        bottom.addWidget(self.button('닫기',self.close))
        layout.addLayout(bottom)
        self.images.currentCellChanged.connect(self.select_image)
        self.targets.currentCellChanged.connect(self.select_target)
        self.runner.changed.connect(self.reload)
        self.timer=QTimer(self);self.timer.setInterval(4000);self.timer.timeout.connect(self.reload);self.timer.start()
        self.reload()

    def button(self,text,action):
        b=QPushButton(text);b.clicked.connect(action);return b

    def table(self,headers):
        table=QTableWidget(0,len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setWordWrap(False)
        return table

    def fill(self,table,rows,columns,identities=None):
        selected=table.currentRow()
        selected_id=table.item(selected,0).data(Qt.ItemDataRole.UserRole) if selected>=0 and table.item(selected,0) else None
        table.blockSignals(True)
        table.setRowCount(len(rows))
        new_selected=-1
        for r,values in enumerate(rows):
            for c,value in enumerate(values):
                item=QTableWidgetItem(str(value or '—'))
                item.setToolTip(str(value or ''))
                if c==0:
                    identity=identities[r] if identities else r
                    item.setData(Qt.ItemDataRole.UserRole,identity)
                    if identity==selected_id:new_selected=r
                table.setItem(r,c,item)
        if new_selected>=0:table.selectRow(new_selected)
        table.blockSignals(False)

    def fail(self,message):
        self.status.setText('작업을 완료하지 못했습니다 · '+message)

    def reload(self):
        if self.closed:return
        if self.snapshot_pending:
            self.reload_requested=True
            return
        self.snapshot_pending=True
        target=self.filter.currentData();offset=self.offset
        def load(_):
            return self.repo.reviewed(),self.repo.candidates(),self.repo.targets(),self.repo.images(target,offset),self.repo.jobs()
        def done(result):
            self.snapshot_pending=False
            if target!=self.filter.currentData() or offset!=self.offset:
                self.reload_requested=False
                self.reload()
                return
            reviewed,self.candidate_rows,self.target_rows,self.image_rows,self.job_rows=result
            self.status.setText('공개 API 연결 준비 완료 · 사진별 이용 조건 및 등장 인물은 별도로 확인합니다.' if reviewed else '최신 제공처 정책/API 확인 대기 · 실제 자동 수집은 아직 활성화하지 않았습니다. 로컬 라이브러리는 계속 사용할 수 있습니다.')
            self.fill(self.candidates,[[x['name'],'그룹' if x['kind']=='group' else '인물',x['external_id'],{'pending':'검토 대기','conflict':'연결 충돌'}.get(x['status'],x['status']),x['reason']] for x in self.candidate_rows],None,[x['id'] for x in self.candidate_rows])
            self.fill(self.targets,[[x['name'],'그룹' if x['kind']=='group' else '인물',x['external_id'],x['category']] for x in self.target_rows],None,[x['id'] for x in self.target_rows])
            self.fill(self.images,[[x['title'],f"{x['width']} × {x['height']}",x['license_name']+(' · 검토 필요' if x['license_status']!='eligible' else '') if x['license_name'] else '검토 필요','보관됨' if x['local_asset_id'] else '온라인'] for x in self.image_rows],None,[x['id'] for x in self.image_rows])
            if self.selected_remote:
                current_remote=next((x for x in self.image_rows if x['id']==self.selected_remote['id']),None)
                self.selected_remote=current_remote
                self.download.setEnabled(bool(current_remote and current_remote['license_status']=='eligible'))
                self.source.setEnabled(current_remote is not None)
                if current_remote is None:
                    self.generation+=1
                    if self.preview_job:self.preview_job.cancelled.set()
                    self.preview.clear();self.preview.setText('사진을 선택하세요')
                    self.details.setText('등장 인물은 자동 확정하지 않습니다.')
            self.fill(self.jobs,[[KIND_LABELS.get(x['kind'],x['kind']),STATES.get(x['state'],x['state']),x['attempts'],job_summary(x)] for x in self.job_rows],None,[x['id'] for x in self.job_rows])
            current=self.filter.currentData()
            self.filter.blockSignals(True);self.filter.clear();self.filter.addItem('모든 등록 대상',None)
            for x in self.target_rows:self.filter.addItem(x['name'],x['id'])
            index=self.filter.findData(current)
            self.filter.setCurrentIndex(max(0,index));self.filter.blockSignals(False)
            if self.reload_requested:
                self.reload_requested=False
                self.reload()
        def error(message):self.snapshot_pending=False;self.fail(message)
        self.tasks.submit(load,done,error)

    def enqueue(self,kind,payload,key=None):
        def done(_):
            self.runner.tick(manual=True);self.reload()
            self.tabs.setCurrentWidget(self.jobs)
        self.tasks.submit(lambda _:self.repo.enqueue(kind,payload,key=key,force=True),done,self.fail)

    def discover(self):self.enqueue('discover',{})

    def add_qid(self):
        dialog=QDialog(self);dialog.setWindowTitle('외부 식별자로 갱신 대상 찾기')
        form=QFormLayout(dialog)
        identity=QLineEdit();identity.setPlaceholderText('Wikidata Q ID · 예: Q…')
        kind=QComboBox();kind.addItem('그룹','group');kind.addItem('인물','person')
        form.addRow('외부 식별자',identity);form.addRow('유형',kind)
        form.addRow(QLabel('이름만으로 기존 인물과 병합하지 않습니다. 조회 후 등록 검토에 표시합니다.'))
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if not dialog.exec():return
        try:external=qid(identity.text().strip())
        except ValueError as e:self.fail(str(e));return
        candidate=dict(external_id=external,kind=kind.currentData(),name=external)
        def work(_):
            self.repo.candidate(candidate)
            self.repo.enqueue('enrich',{'ids':[external]},key='enrich:'+external,force=True)
        self.tasks.submit(work,lambda _:(self.runner.tick(manual=True),self.reload()),self.fail)

    def refresh_target(self):
        index=self.targets.currentRow()
        if not 0<=index<len(self.target_rows):self.fail('갱신 대상을 선택하세요.');return
        target=self.target_rows[index]
        self.enqueue('refresh',{'target_id':target['id']})

    def select_target(self,row,*_):
        if 0<=row<len(self.target_rows):
            self.filter.setCurrentIndex(self.filter.findData(self.target_rows[row]['id']))

    def filter_changed(self):
        self.offset=0
        self.selected_remote=None
        self.download.setEnabled(False)
        self.source.setEnabled(False)
        self.reload()

    def page(self,delta):
        if delta>0 and len(self.image_rows)<200:return
        self.offset=max(0,self.offset+delta);self.reload()

    def accept(self):
        index=self.candidates.currentRow()
        if not 0<=index<len(self.candidate_rows):self.fail('등록 검토에서 후보를 선택하세요.');return
        candidate=self.candidate_rows[index]
        def load(_):return self.repo.candidate_item(candidate['external_id']),self.service.db.entities()['groups' if candidate['kind']=='group' else 'persons']
        self.tasks.submit(load,lambda result:self.accept_details(candidate,*result),self.fail)

    def accept_details(self,candidate,payload,entities):
        dialog=QDialog(self);dialog.setWindowTitle('인물·그룹 확인 후 연결')
        form=QFormLayout(dialog)
        candidate_label=QLabel(candidate['name']+' · '+candidate['external_id'])
        candidate_label.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow(candidate_label)
        detail=QLabel('출처: https://www.wikidata.org/wiki/'+candidate['external_id']+'\n소속 후보: '+', '.join(payload.get('groups',[]))+'\n멤버 후보: '+', '.join(payload.get('members',[])))
        detail.setTextFormat(Qt.TextFormat.PlainText);detail.setWordWrap(True);form.addRow(detail)
        local=QComboBox();local.addItem('새 항목으로 등록',None)
        for item in entities:local.addItem(item['name'],item['id'])
        form.addRow('기존 항목 연결',local)
        gender=QComboBox()
        for label,value in [('알 수 없음','unknown'),('여성','female'),('남성','male'),('기타','other')]:gender.addItem(label,value)
        solo=QCheckBox('확인된 솔로 활동 인물')
        group_kind=QComboBox()
        for label,value in [('유형 확인 필요','other'),('걸그룹','girl'),('혼성그룹','mixed')]:group_kind.addItem(label,value)
        if candidate['kind']=='person':form.addRow('확인된 성별',gender);form.addRow(solo)
        else:form.addRow('확인된 그룹 유형',group_kind)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if not dialog.exec():return
        identity,gender_value,solo_value,group_value=local.currentData(),gender.currentData(),solo.isChecked(),group_kind.currentData()
        def work(_):return self.repo.accept(candidate['external_id'],identity,gender_value,solo_value,group_value)
        def done(_):self.library_changed.emit();self.reload()
        self.tasks.submit(work,done,self.fail)

    def select_image(self,index,*_):
        self.generation+=1;generation=self.generation
        if self.preview_job:self.preview_job.cancelled.set()
        if not 0<=index<len(self.image_rows):return
        row=self.image_rows[index];self.selected_remote=row
        self.preview.clear();self.preview.setText('썸네일 불러오는 중…')
        self.person_confirmed.setChecked(False)
        self.details.setText(f"{row['title']}\n{row['width']} × {row['height']}\n이용 조건: {row['license_name'] or '확인 필요'}\n저작자/표시: {row['attribution'] or '확인 필요'}\n추가 제한: {row['restrictions'] or '제공처 별도 확인'}\n촬영일: {row['captured'] or '알 수 없음'}\n업로드 시각: {row['published'] or '알 수 없음'}\n{row['source_url']}\n\n카테고리 관련 후보이며 등장 인물은 미확인입니다.")
        self.download.setEnabled(row['license_status']=='eligible')
        self.source.setEnabled(True)
        def done(image):
            if generation==self.generation:
                self.preview.setPixmap(QPixmap.fromImage(image).scaled(400,280,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        def error(message):
            if generation==self.generation:self.preview.setText('썸네일을 표시할 수 없습니다.\n'+message)
        self.preview_job=self.preview_tasks.submit(lambda cancel:self.service.thumbnail(row['id'],cancel),done,error)

    def download_original(self):
        if not self.selected_remote:return
        self.enqueue('download',dict(remote_id=self.selected_remote['id'],target_id=self.filter.currentData(),person_confirmed=self.person_confirmed.isChecked()))

    def open_source(self):
        from urllib.parse import urlsplit
        if self.selected_remote:
            url=self.selected_remote['source_url'];parsed=urlsplit(url)
            if parsed.scheme=='https' and parsed.hostname=='commons.wikimedia.org':QDesktopServices.openUrl(QUrl(url))

    def retry(self):
        index=self.jobs.currentRow()
        if not 0<=index<len(self.job_rows):self.fail('재시도할 작업을 선택하세요.');return
        identity=self.job_rows[index]['id']
        self.tasks.submit(lambda _:self.repo.retry(identity),lambda _:(self.runner.tick(manual=True),self.reload()),self.fail)

    def auto_register_changed(self,value):
        self.settings.values['auto_register']=value
        try:self.settings.save()
        except OSError as e:self.fail(str(e))

    def automatic_changed(self,value):
        self.settings.values['online_enabled']=value
        try:self.settings.save()
        except OSError as e:self.fail(str(e))
        if not value:self.runner.pause()

    def shutdown(self):
        if self.closed:return
        self.closed=True
        try:self.runner.changed.disconnect(self.reload)
        except (RuntimeError,TypeError):pass
        self.generation+=1
        self.timer.stop()
        self.tasks.stop();self.preview_tasks.stop()

    def done(self,result):self.shutdown();super().done(result)
    def closeEvent(self,event):self.shutdown();super().closeEvent(event)
