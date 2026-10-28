"""Minimal multi-source workspace; all IO and catalog reads run off the UI thread."""
import hashlib
import html
import json
from pathlib import Path
from urllib.parse import urlsplit
from PySide6.QtCore import Qt,Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QComboBox,QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QSplitter,QDialog,QFormLayout,QDialogButtonBox,QCheckBox,QFileDialog,QListWidget,QListWidgetItem)
from .workers import Tasks
from .source_specs import SourceSpec,OPERATIONS,STATES,OPERATION_LABELS
from .page_discovery import clean_url

OP_LABELS=OPERATION_LABELS
STATE_LABELS={'allowed':'확인됨','restricted':'제한됨','review':'확인 필요','denied':'허용되지 않음'}
ROLE_LABELS={'page_image':'페이지 이미지','responsive':'출처가 선언한 반응형 대체 버전','source_declared_original':'출처가 원본으로 표기 · 미확정','page_metadata':'페이지 메타데이터 이미지','thumbnail':'출처가 제공한 썸네일','provided_version':'사용자가 지정한 이미지 URL'}
CATEGORY_LABELS={'official':'공식 사진','social':'SNS','press':'언론사','fan':'팬 촬영','user':'사용자 등록'}

class SourcePane(QWidget):
    library_changed=Signal()
    def __init__(self,service,runner,parent=None):
        super().__init__(parent)
        self.service,self.web,self.runner=service,service.web,runner
        self.repo=self.web.repo
        self.tasks=Tasks(self,1);self.previews=Tasks(self,1)
        self.closed=False;self.loading=False;self.again=False;self.generation=0
        self.rows=[];self.selected=None;self.offset=0;self.preview_job=None
        outer=QVBoxLayout(self)
        top=QHBoxLayout()
        self.sources=QComboBox();self.sources.addItem('전체 출처 / URL에서 자동 등록',None)
        self.sources.currentIndexChanged.connect(self.source_changed)
        top.addWidget(self.sources,1)
        top.addWidget(self.button('출처 설정 추가',self.add_source))
        top.addWidget(self.button('설정 JSON 가져오기',self.import_spec))
        top.addWidget(self.button('출처 권한 확인',lambda:self.permissions(None)))
        outer.addLayout(top)
        links=QHBoxLayout()
        self.url=QLineEdit();self.url.setPlaceholderText('공개 페이지 또는 이미지의 HTTPS URL · 등록만으로 다운로드하지 않습니다.')
        links.addWidget(self.url,1)
        self.mode=QComboBox()
        for name,key in [('공개 HTML','html'),('JavaScript 페이지','rendered'),('직접 이미지 URL','direct')]:self.mode.addItem(name,key)
        links.addWidget(self.mode)
        links.addWidget(self.button('링크 등록',self.add_url))
        outer.addLayout(links)
        pages=QHBoxLayout()
        self.pages=QComboBox();self.pages.addItem('전체 등록 페이지',None)
        self.pages.currentIndexChanged.connect(self.page_changed)
        pages.addWidget(self.pages,1)
        pages.addWidget(self.button('허용된 페이지 탐색',self.scan))
        pages.addWidget(self.button('이전',lambda:self.paginate(-200)))
        pages.addWidget(self.button('다음',lambda:self.paginate(200)))
        outer.addLayout(pages)
        self.status=QLabel('출처를 선택해 연결 방식과 이용 조건을 확인하세요.')
        self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText);outer.addWidget(self.status)
        split=QSplitter();outer.addWidget(split,1)
        self.table=QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(['이미지 후보','실측 해상도','페이지 선언값','보관'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.currentCellChanged.connect(self.select)
        split.addWidget(self.table)
        side=QWidget();right=QVBoxLayout(side)
        self.preview=QLabel('후보 선택 · 확인된 이미지 캐시만 표시')
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter);self.preview.setMinimumSize(220,170);self.preview.setTextFormat(Qt.TextFormat.PlainText)
        right.addWidget(self.preview)
        self.details=QLabel('원본·다운로드·분석 권한을 각각 관리합니다.')
        self.details.setWordWrap(True);self.details.setMinimumWidth(200);self.details.setMaximumWidth(430);self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.details,1)
        right.addWidget(self.button('이 사진 권한 확인',lambda:self.permissions(self.selected)))
        right.addWidget(self.button('허용된 파일 실측 검사',lambda:self.queue('web_probe')))
        right.addWidget(self.button('검사된 최적 버전 선택',self.best))
        right.addWidget(self.button('선택 버전 보관',self.download))
        right.addWidget(self.button('허용된 유사 후보 검사',lambda:self.queue('web_analyze')))
        split.addWidget(side);split.setSizes([700,340])
        note=QLabel('선언값·URL 문구는 실측이 아닙니다. 사진 후보가 인물의 실제 등장을 확정하지 않으며, 유사도만으로 삭제·병합하지 않습니다.')
        note.setWordWrap(True);note.setObjectName('muted');outer.addWidget(note)
        self.runner.changed.connect(self.reload)
        self.reload()

    def button(self,label,action):
        widget=QPushButton(label);widget.clicked.connect(action);return widget

    def fail(self,message):self.status.setText(str(message))

    def source_changed(self):
        self.offset=0;self.selected=None
        self.pages.blockSignals(True);self.pages.clear();self.pages.addItem('전체 등록 페이지',None);self.pages.blockSignals(False)
        self.reload()

    def page_changed(self):self.offset=0;self.selected=None;self.reload()
    def paginate(self,delta):
        if delta>0 and len(self.rows)<200:return
        self.offset=max(0,self.offset+delta);self.selected=None;self.reload()

    def reload(self):
        if self.closed:return
        if self.loading:self.again=True;return
        self.loading=True
        source,page,offset=self.sources.currentData(),self.pages.currentData(),self.offset
        def work(_):
            profiles=self.repo.profiles()
            permissions=self.repo.permissions(source) if source else None
            return profiles,self.repo.pages(source),self.repo.images(source,page,offset),permissions
        def done(result):
            self.loading=False
            if source!=self.sources.currentData() or page!=self.pages.currentData() or offset!=self.offset:
                self.again=False;self.reload();return
            profiles,pages,self.rows,permissions=result
            self.sources.blockSignals(True);self.sources.clear();self.sources.addItem('전체 출처 / URL에서 자동 등록',None)
            for item in profiles:
                if item['adapter']=='local':continue
                label=item['name']+(' · 검토 후보' if item['adapter']=='candidate' else (' · 사용 중' if item['enabled'] else ' · 연결 대기'))
                self.sources.addItem(label,item['id'])
            self.sources.setCurrentIndex(max(0,self.sources.findData(source)));self.sources.blockSignals(False)
            self.pages.blockSignals(True);self.pages.clear();self.pages.addItem('전체 등록 페이지',None)
            for item in pages:self.pages.addItem(item['title'] or item['url'],item['id'])
            self.pages.setCurrentIndex(max(0,self.pages.findData(page)));self.pages.blockSignals(False)
            selected_id=self.selected['id'] if self.selected else None
            self.table.blockSignals(True);self.table.setRowCount(len(self.rows));selected_index=-1
            for index,item in enumerate(self.rows):
                values=[item['title'] or item['url'],f"{item['width']} × {item['height']}" if item['width'] else '미검사',f"{item['declared_width'] or '?'} × {item['declared_height'] or '?'}",'보관됨' if item['local_asset_id'] else '후보']
                for column,value in enumerate(values):
                    cell=QTableWidgetItem(value);cell.setToolTip('<span>'+html.escape(value)+'</span>');self.table.setItem(index,column,cell)
                if item['id']==selected_id:selected_index=index
            if selected_index>=0:self.table.selectRow(selected_index);self.selected=self.rows[selected_index]
            else:self.selected=None
            self.table.blockSignals(False)
            if permissions:
                self.status.setText(' · '.join(OP_LABELS[key]+': '+STATE_LABELS[value['state']] for key,value in permissions.items()))
            else:self.status.setText(f'후보 {len(self.rows)}개 · 사이트/사진별 권한이 확인된 기능만 실행됩니다.')
            if self.again:self.again=False;self.reload()
        def error(message):self.loading=False;self.fail(message)
        self.tasks.submit(work,done,error)

    def add_url(self):
        url=clean_url(self.url.text().strip(),self.url.text().strip())
        if not url:self.fail('공개 HTTPS URL을 입력하세요. 로컬/사설 주소와 로그인 자격증명은 지원하지 않습니다.');return
        source=self.sources.currentData();mode=self.mode.currentData()
        def work(_):
            nonlocal source
            if source is None:
                host=urlsplit(url).hostname
                source='user-'+hashlib.sha256(host.encode()).hexdigest()[:16]
                profiles={x['id'] for x in self.repo.profiles()}
                if source not in profiles:self.repo.save_spec(dict(id=source,name=host,category='user',adapter=mode,domains=[host]))
            identity=self.repo.add_page(source,url,mode)
            return source,identity
        def done(result):
            self.status.setText('링크를 등록했습니다. 권한 확인 전에는 이미지를 받지 않습니다.')
            self.url.clear();self.reload()
        self.tasks.submit(work,done,self.fail)

    def add_source(self):
        dialog=QDialog(self);dialog.setWindowTitle('검토할 도메인 출처 등록');form=QFormLayout(dialog)
        name=QLineEdit();hosts=QLineEdit();policy=QLineEdit()
        hosts.setPlaceholderText('정확한 페이지·이미지 도메인, 쉼표로 구분 · 와일드카드 불가')
        category=QComboBox()
        for key,label in CATEGORY_LABELS.items():category.addItem(label,key)
        adapter=QComboBox()
        for key,label in [('html','공개 HTML'),('rendered','JavaScript DOM · 선택적 브라우저 필요'),('direct','직접 이미지 URL')]:adapter.addItem(label,key)
        form.addRow('사이트 이름',name);form.addRow('정확한 도메인',hosts);form.addRow('분류',category);form.addRow('접근 방식',adapter);form.addRow('이용 조건 URL',policy)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if not dialog.exec():return
        domains=[x.strip().lower() for x in hosts.text().split(',') if x.strip()]
        data=dict(id='site-'+hashlib.sha256(','.join(sorted(domains)).encode()).hexdigest()[:16],name=name.text().strip(),category=category.currentData(),adapter=adapter.currentData(),domains=domains,policy_urls=[policy.text().strip()] if policy.text().strip() else [])
        self.tasks.submit(lambda _:self.repo.save_spec(data),lambda _:self.reload(),self.fail)

    def import_spec(self):
        path,_=QFileDialog.getOpenFileName(self,'선언형 출처 설정 가져오기','','JSON (*.json)')
        if not path:return
        def work(_):
            if Path(path).stat().st_size>64*1024:raise ValueError('설정 파일이 너무 큽니다.')
            return self.repo.save_spec(json.loads(Path(path).read_text('utf-8')))
        self.tasks.submit(work,lambda _:self.reload(),self.fail)

    def permissions(self,item):
        source=item['source_id'] if item else self.sources.currentData()
        if not source:self.fail('권한을 확인할 출처 또는 사진을 먼저 선택하세요.');return
        resource='web:'+item['id'] if item else None
        def work(_):
            profile=next(x for x in self.repo.profiles() if x['id']==source)
            return self.repo.permissions(source,resource),profile
        def open_dialog(result):
            existing,profile=result
            dialog=QDialog(self);dialog.setWindowTitle('사진별 이용 권한' if item else '출처 이용 권한')
            form=QFormLayout(dialog);combos={}
            info=QLabel('페이지에 사진이 보인다는 사실만으로 다운로드·AI 이용이 허용되는 것은 아닙니다. 실제로 확인한 범위만 기록하세요.')
            info.setWordWrap(True);form.addRow(info)
            for key in OPERATIONS:
                if item and key=='browser_render':continue
                combo=QComboBox()
                for state in STATES:combo.addItem(STATE_LABELS[state],state)
                combo.setCurrentIndex(combo.findData(existing[key]['state']));combos[key]=combo
                form.addRow(OP_LABELS[key],combo)
            reference=QLineEdit();reference.setPlaceholderText('실제 확인한 이용 조건 URL 또는 문서 식별자')
            note=QLineEdit();note.setPlaceholderText('사용 허가 범위·저작자 표시·추가 제한 등 실제 확인 내용')
            form.addRow('확인 근거',reference);form.addRow('확인 내용',note)
            attribution=QLineEdit();license_url=QLineEdit()
            if item:
                form.addRow('저작자·표시 문구',attribution);form.addRow('파일별 이용 조건 URL',license_url)
            enabled=QCheckBox('검토한 출처 사용');enabled.setChecked(bool(profile['enabled']))
            if not item:
                enabled.setEnabled(profile['adapter']!='candidate');form.addRow(enabled)
            buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
            if not dialog.exec():return
            values={key:combo.currentData() for key,combo in combos.items()};evidence=dict(reference=reference.text().strip(),note=note.text().strip(),attribution=attribution.text().strip(),license_url=license_url.text().strip());enable=enabled.isChecked()
            def save(_):
                self.repo.grant_many(source,values,evidence,resource,enable if not item else None)
            self.tasks.submit(save,lambda _:self.reload(),self.fail)
        self.tasks.submit(work,open_dialog,self.fail)

    def scan(self):
        identity=self.pages.currentData()
        if not identity:self.fail('탐색할 등록 페이지를 선택하세요.');return
        self.enqueue('web_scan',{'page_id':identity})

    def enqueue(self,kind,payload):
        def done(identity):self.runner.request(identity,'analysis' if kind=='web_analyze' else 'network');self.reload()
        self.tasks.submit(lambda _:self.service.repo.enqueue(kind,payload,force=True),done,self.fail)

    def queue(self,kind):
        if not self.selected:self.fail('이미지 후보를 선택하세요.');return
        self.enqueue(kind,{'item_id':self.selected['id']})

    def select(self,row,*_):
        self.generation+=1;generation=self.generation
        if self.preview_job:self.preview_job.cancelled.set()
        if not 0<=row<len(self.rows):self.selected=None;return
        item=self.rows[row];self.selected=item
        self.preview.clear();self.preview.setText('확인된 캐시 표시 중…')
        def info(_):
            return self.repo.permissions(item['source_id'],'web:'+item['id']),self.repo.matches(item['id']),self.repo.proposals(item['id'])
        def details(result):
            if generation!=self.generation:return
            rights,matches,proposals=result
            self.details.setText(f"{item['page_url']}\n{item['url']}\n실측: {str(item['width'])+' × '+str(item['height']) if item['width'] else '미검사'}\n버전 표기: {ROLE_LABELS.get(item['role'],'미확정 버전')} · 원본 확정 아님\n"+'\n'.join(OP_LABELS[k]+': '+STATE_LABELS[v['state']] for k,v in rights.items() if k!='browser_render')+f"\n유사/중복 관계 {len(matches)}개 · 유사 후보는 동일 사진의 증명이 아닙니다.\n언급 후보(등장 미확인): "+(", ".join(x['name'] for x in proposals) or '없음')+f"\n게시일: {item.get('page_published') or '알 수 없음'} · 촬영일은 추정하지 않습니다.")
        self.tasks.submit(info,details,self.fail)
        def show(image):
            if generation==self.generation:self.preview.setPixmap(QPixmap.fromImage(image).scaled(380,240,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        def error(message):
            if generation==self.generation:self.preview.setText(message)
        self.preview_job=self.previews.submit(lambda cancel:self.web.preview(item['id'],cancel),show,error)

    def best(self):
        if not self.selected:return
        identity=self.selected['id']
        def done(item):
            if not item:self.fail('권한과 실제 크기가 확인된 버전이 없습니다. 먼저 허용된 파일을 검사하세요.');return
            for row,value in enumerate(self.rows):
                if value['id']==item['id']:self.table.selectRow(row);self.select(row);break
            self.status.setText(f"검사된 버전 중 {item['width']} × {item['height']} 선택 · URL 문자열로 원본을 추정하지 않았습니다.")
        self.tasks.submit(lambda _:self.web.best(identity),done,self.fail)

    def download(self):
        if not self.selected:return
        identity=self.selected['id']
        def open_dialog(entities):
            dialog=QDialog(self);dialog.setWindowTitle('사진에 등장하는 인물 확인');layout=QVBoxLayout(dialog)
            label=QLabel('실제 등장하는 인물만 선택하세요. 아무도 확정하지 않고 보관할 수도 있습니다. 제목·설명에 이름이 있다는 사실만으로 선택하지 마세요.')
            label.setWordWrap(True);layout.addWidget(label)
            people=QListWidget()
            for person in entities:
                cell=QListWidgetItem(person['name']);cell.setData(Qt.ItemDataRole.UserRole,person['id']);cell.setCheckState(Qt.CheckState.Unchecked);people.addItem(cell)
            layout.addWidget(people)
            buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
            if not dialog.exec():return
            confirmed=[people.item(row).data(Qt.ItemDataRole.UserRole) for row in range(people.count()) if people.item(row).checkState()==Qt.CheckState.Checked]
            self.enqueue('web_download',dict(item_id=identity,confirmed_people=confirmed))
        self.tasks.submit(lambda _:self.service.db.entities()['persons'],open_dialog,self.fail)

    def shutdown(self):
        if self.closed:return
        self.closed=True;self.generation+=1
        try:self.runner.changed.disconnect(self.reload)
        except (RuntimeError,TypeError):pass
        self.tasks.stop();self.previews.stop();self.preview.clear()
