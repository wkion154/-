from PySide6.QtCore import Qt
from .workers import Tasks
from PySide6.QtWidgets import QDialog,QFormLayout,QLineEdit,QComboBox,QCheckBox,QDialogButtonBox,QMessageBox,QPushButton,QFileDialog,QSpinBox,QLabel

class EntityDialog(QDialog):
    def __init__(self, db, kind, parent=None, existing=None):
        super().__init__(parent)
        self.db, self.kind, self.existing = db, kind, existing
        self.tasks=Tasks(self,1)
        self.setWindowTitle(('그룹' if kind=='group' else '인물')+(' 수정' if existing else ' 추가'))
        self.setMinimumWidth(400)
        form = QFormLayout(self)
        self.name = QLineEdit(existing['name'] if existing else '')
        self.aliases = QLineEdit(existing['aliases'] if existing else '')
        form.addRow('표시 이름', self.name)
        form.addRow('별칭 · 쉼표로 구분', self.aliases)
        self.kindbox = QComboBox()
        options = [('걸그룹','girl'),('혼성그룹','mixed'),('기타','other')] if kind=='group' else [('알 수 없음','unknown'),('여성','female'),('남성','male'),('기타','other')]
        for text,value in options:
            self.kindbox.addItem(text,value)
        self.solo = QCheckBox('솔로 활동 아티스트')
        self.group = QComboBox()
        self.group.addItem('연결하지 않음',None)
        for g in db.entities()['groups']:
            self.group.addItem(g['name'],g['id'])
        self.target = QCheckBox('그룹 기본 갤러리 대상')
        self.target.setChecked(True)
        if not existing:
            form.addRow('유형' if kind=='group' else '성별',self.kindbox)
            if kind!='group':
                form.addRow(self.solo)
                form.addRow('소속 그룹',self.group)
                form.addRow(self.target)
        self.error = QLabel()
        self.error.setWordWrap(True)
        form.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
    def save(self):
        name,aliases=self.name.text(),self.aliases.text()
        kind,existing=self.kind,self.existing
        gender,solo,group,target=self.kindbox.currentData(),self.solo.isChecked(),self.group.currentData(),self.target.isChecked()
        def operation(_):
            if existing:
                self.db.rename(kind,existing['id'],name,aliases)
            else:
                self.db.add_entity(kind,name,aliases,gender,solo,group,target)
        self.setEnabled(False)
        def done(_):
            self.setEnabled(True)
            self.accept()
        def error(message):
            self.setEnabled(True)
            self.error.setText(message)
        self.tasks.submit(operation,done,error)
    def done(self,result):
        self.tasks.stop()
        super().done(result)

class LinkDialog(QDialog):
    def __init__(self,db,parent=None):
        super().__init__(parent)
        self.setWindowTitle('그룹 멤버 연결 관리')
        self.setMinimumWidth(400)
        self.db=db
        self.tasks=Tasks(self,1)
        form=QFormLayout(self)
        entities=db.entities()
        self.person=QComboBox()
        self.group=QComboBox()
        for p in entities['persons']:self.person.addItem(p['name'],p['id'])
        for g in entities['groups']:self.group.addItem(g['name'],g['id'])
        self.target=QCheckBox('그룹 기본 갤러리 대상')
        self.target.setChecked(True)
        self.unlink=QCheckBox('이 그룹의 연결 해제')
        form.addRow('인물',self.person)
        form.addRow('그룹',self.group)
        form.addRow(self.target)
        form.addRow(self.unlink)
        self.error=QLabel()
        form.addRow(self.error)
        b=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        b.accepted.connect(self.save)
        b.rejected.connect(self.reject)
        form.addRow(b)
    def save(self):
        person,group=self.person.currentData(),self.group.currentData()
        target,unlink=self.target.isChecked(),self.unlink.isChecked()
        if not person or not group:
            self.error.setText('그룹과 인물을 먼저 등록하세요.')
            return
        self.setEnabled(False)
        def done(_):
            self.setEnabled(True)
            self.accept()
        def error(message):
            self.setEnabled(True)
            self.error.setText(message)
        self.tasks.submit(lambda _:self.db.link_member(person,group,target,unlink),done,error)
    def done(self,result):
        self.tasks.stop()
        super().done(result)

class ImportDialog(QDialog):
    def __init__(self,db,count,parent=None):
        super().__init__(parent)
        self.setWindowTitle('사진 가져오기')
        self.setMinimumWidth(430)
        form=QFormLayout(self)
        form.addRow(QLabel(f'선택한 경로 {count}개 · 폴더는 하위 폴더까지 확인합니다.'))
        self.group=QComboBox()
        self.person=QComboBox()
        self.group.addItem('연결 없음',None)
        self.person.addItem('연결 없음',None)
        entities=db.entities()
        for g in entities['groups']:self.group.addItem(g['name'],g['id'])
        for p in entities['persons']:self.person.addItem(p['name'],p['id'])
        form.addRow('그룹',self.group)
        form.addRow('등장 인물 · 직접 확인',self.person)
        self.managed=QCheckBox('앱 관리 저장소로 원본 복사')
        form.addRow(self.managed)
        label=QLabel('기본값은 외부 파일 참조입니다. 원본을 이동하거나 수정하지 않습니다.\n인물 연결은 선택한 모든 사진에 적용되므로 등장 여부를 확인하세요.')
        label.setWordWrap(True)
        form.addRow(label)
        b=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        b.accepted.connect(self.accept)
        b.rejected.connect(self.reject)
        form.addRow(b)

class SettingsDialog(QDialog):
    def __init__(self,settings,parent=None):
        super().__init__(parent)
        self.setWindowTitle('라이브러리 설정')
        self.setMinimumWidth(520)
        self.settings=settings
        form=QFormLayout(self)
        self.paths={}
        for key,label,current in [('cache_path','썸네일 캐시',settings.cache),('storage_path','영구 원본 저장소',settings.storage)]:
            edit=QLineEdit(str(current))
            self.paths[key]=edit
            form.addRow(label,edit)
            button=QPushButton('폴더 선택')
            button.clicked.connect(lambda _,e=edit:self.choose(e))
            form.addRow('',button)
        self.mode=QComboBox()
        self.mode.addItem('화면 맞춤 · 빠른 미리보기','fit')
        self.mode.addItem('원본 픽셀 · 전체 해상도','actual')
        self.mode.setCurrentIndex(1 if settings.values['viewer_mode']=='actual' else 0)
        form.addRow('뷰어 시작 방식',self.mode)
        label=QLabel('차콜 다크 테마 · 로컬 라이브러리 Alpha 2\n캐시 경로 변경은 기존 파일을 이동·삭제하지 않습니다.\n온라인 수집 및 AI 기능은 후속 단계입니다.')
        label.setWordWrap(True)
        form.addRow(label)
        b=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        b.accepted.connect(self.accept)
        b.rejected.connect(self.reject)
        form.addRow(b)
    def choose(self,edit):
        path=QFileDialog.getExistingDirectory(self,'저장 폴더 선택',edit.text())
        if path:edit.setText(path)
    def accept(self):
        from pathlib import Path
        try:
            cache=Path(self.paths['cache_path'].text()).expanduser().resolve()
            storage=Path(self.paths['storage_path'].text()).expanduser().resolve()
            if cache==storage or cache in storage.parents or storage in cache.parents:
                raise ValueError('캐시와 영구 저장소는 서로 겹치지 않는 폴더를 선택하세요.')
            for path in (cache,storage):
                path.mkdir(parents=True,exist_ok=True)
            self.settings.values.update({k:str(Path(v.text()).expanduser().resolve()) for k,v in self.paths.items()})
            self.settings.values['viewer_mode']=self.mode.currentData()
            self.settings.save()
            super().accept()
        except Exception as error:QMessageBox.warning(self,'설정 저장 실패',str(error))
