import logging
import threading
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QByteArray,Signal,QPoint
from PySide6.QtGui import QShortcut,QKeySequence,QGuiApplication
from PySide6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QLineEdit,QComboBox,QFrame,QSplitter,QTreeWidget,QTreeWidgetItem,QSlider,QFileDialog,QMessageBox,QDialog,QMenu,QScrollArea)
from .gallery import GalleryModel,GalleryView
from .dialogs import EntityDialog,LinkDialog,ImportDialog,SettingsDialog
from .viewer import Viewer
from .workers import Tasks
from .images import import_paths
from .providers.http import HttpClient
from .providers.wikimedia import WikimediaProvider
from .online_service import OnlineService
from .online_runner import OnlineRunner
from .online_dialog import OnlineDialog

class MainWindow(QMainWindow):
    import_progress = Signal(int)
    def __init__(self,db,settings):
        super().__init__()
        self.db,self.settings=db,settings
        self.setWindowTitle('K-IDOL ARCHIVE · Alpha 3')
        self.resize(1440,920)
        self.setMinimumSize(820,560)
        self.scope=None
        self.section='all'
        self.selected=None
        self.import_job=None
        self.bookmark=None
        self.tasks=Tasks(self,1)
        self.meta_tasks=Tasks(self,1)
        self.central=QWidget()
        self.setCentralWidget(self.central)
        outer=QVBoxLayout(self.central)
        outer.setContentsMargins(0,0,0,0)
        outer.setSpacing(0)
        toolbar=QFrame()
        toolbar.setObjectName('toolbar')
        top=QHBoxLayout(toolbar)
        top.setContentsMargins(20,12,20,12)
        self.side_toggle=self.button('☰',self.toggle_sidebar)
        top.addWidget(self.side_toggle)
        brand=QLabel('K-IDOL  /  ARCHIVE')
        brand.setObjectName('brand')
        top.addWidget(brand)
        top.addSpacing(20)
        self.search=QLineEdit()
        self.search.setPlaceholderText('그룹, 인물, 별칭, 파일명 검색  ·  Ctrl+K')
        self.search.setClearButtonEnabled(True)
        top.addWidget(self.search,1)
        self.info_toggle=self.button('정보 패널',self.toggle_info)
        top.addWidget(self.info_toggle)
        top.addWidget(self.button('온라인',self.open_online))
        top.addWidget(self.button('설정',self.open_settings))
        outer.addWidget(toolbar)
        self.splitter=QSplitter()
        outer.addWidget(self.splitter,1)
        self.sidebar=QWidget()
        self.sidebar.setMinimumWidth(175)
        side=QVBoxLayout(self.sidebar)
        side.setContentsMargins(16,24,12,16)
        eyebrow=QLabel('LIBRARY')
        eyebrow.setObjectName('eyebrow')
        side.addWidget(eyebrow)
        side.addSpacing(12)
        self.nav={}
        for text,key in [('⌂   홈','all'),('◷   최신 사진','latest'),('◇   그룹','groups'),('○   솔로','solo'),('☆   즐겨찾기','favorite'),('▧   로컬 라이브러리','local')]:
            b=self.button(text,lambda checked=False,k=key:self.navigate(k))
            b.setObjectName('nav')
            b.setCheckable(True)
            side.addWidget(b)
            self.nav[key]=b
        side.addSpacing(20)
        row=QHBoxLayout()
        label=QLabel('ARTISTS')
        label.setObjectName('eyebrow')
        row.addWidget(label,1)
        add=self.button('＋',self.add_menu)
        row.addWidget(add)
        side.addLayout(row)
        self.tree=QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self.select_entity)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.entity_menu)
        side.addWidget(self.tree,1)
        side.addWidget(self.button('멤버 연결 관리',self.memberships))
        label=QLabel('ALPHA 3\n로컬 우선 · 원본 보존')
        label.setObjectName('muted')
        side.addWidget(label)
        self.splitter.addWidget(self.sidebar)
        center=QWidget()
        content=QVBoxLayout(center)
        content.setContentsMargins(22,24,22,12)
        content.setSpacing(12)
        kicker=QLabel('YOUR COLLECTION')
        kicker.setObjectName('eyebrow')
        content.addWidget(kicker)
        head=QHBoxLayout()
        self.heading=QLabel('라이브러리')
        self.heading.setObjectName('heading')
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        head.addWidget(self.heading,1)
        self.import_button=self.button('＋ 사진 가져오기',self.import_menu)
        self.import_button.setObjectName('primary')
        head.addWidget(self.import_button)
        content.addLayout(head)
        self.subtitle=QLabel('직접 모은 순간을, 선명하게.')
        self.subtitle.setObjectName('muted')
        content.addWidget(self.subtitle)
        self.members_widget=QWidget()
        self.members_layout=QHBoxLayout(self.members_widget)
        self.members_layout.setContentsMargins(0,0,0,0)
        self.members_scroll=QScrollArea()
        self.members_scroll.setWidget(self.members_widget)
        self.members_scroll.setWidgetResizable(True)
        self.members_scroll.setFixedHeight(48)
        self.members_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.members_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.members_scroll.hide()
        content.addWidget(self.members_scroll)
        tools=QHBoxLayout()
        self.count=QLabel('0 PHOTOS')
        self.count.setObjectName('muted')
        tools.addWidget(self.count,1)
        self.sort=QComboBox()
        for label,value in [('최신 등록순','newest'),('오래된 등록순','oldest'),('파일명순','name'),('해상도순','resolution')]:self.sort.addItem(label,value)
        self.sort.setCurrentIndex(self.sort.findData(settings.values['sort']))
        refresh=self.button('↻',lambda:self.reload(preserve=True))
        refresh.setToolTip('라이브러리 새로고침')
        tools.addWidget(refresh)
        tools.addWidget(self.sort)
        self.slider=QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(128,384)
        self.slider.setValue(settings.values['thumbnail_size'])
        self.slider.setFixedWidth(105)
        self.slider.setToolTip('썸네일 크기')
        tools.addWidget(self.slider)
        content.addLayout(tools)
        self.model=GalleryModel(db,self)
        self.gallery=GalleryView(self.model,settings.cache,self)
        self.gallery.set_size(self.slider.value())
        content.addWidget(self.gallery,1)
        self.empty=QLabel('아직 사진이 없습니다\n\n사진을 가져오거나 그룹과 인물을 먼저 등록해 보세요.')
        self.empty.setObjectName('empty')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(160)
        content.addWidget(self.empty,1)
        self.empty.hide()
        self.splitter.addWidget(center)
        self.info_panel=QWidget()
        self.info_panel.setMinimumWidth(210)
        info=QVBoxLayout(self.info_panel)
        info.setContentsMargins(20,28,20,20)
        label=QLabel('PHOTO DETAILS')
        label.setObjectName('eyebrow')
        info.addWidget(label)
        info.addSpacing(16)
        self.info_name=QLabel('사진을 선택하세요')
        self.info_name.setObjectName('infoTitle')
        self.info_name.setTextFormat(Qt.TextFormat.PlainText)
        self.info_name.setWordWrap(True)
        info.addWidget(self.info_name)
        self.info_text=QLabel('해상도, 보관 위치와 연결 정보를\n여기에서 확인할 수 있습니다.')
        self.info_text.setObjectName('muted')
        self.info_text.setTextFormat(Qt.TextFormat.PlainText)
        self.info_text.setWordWrap(True)
        self.info_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(self.info_text)
        self.fav_button=self.button('☆ 즐겨찾기',self.toggle_favorite)
        info.addWidget(self.fav_button)
        self.associate_button=self.button('그룹 · 인물 연결',self.associate)
        info.addWidget(self.associate_button)
        self.relink_button=self.button('원본 다시 연결',self.relink)
        info.addWidget(self.relink_button)
        for b in (self.fav_button,self.associate_button,self.relink_button):b.setEnabled(False)
        info.addStretch()
        label=QLabel('원본 파일은 변경하지 않습니다.\n등록일과 촬영일은 별도로 관리합니다.')
        label.setObjectName('muted')
        label.setWordWrap(True)
        info.addWidget(label)
        self.splitter.addWidget(self.info_panel)
        self.splitter.setSizes([220,950,270])
        self.splitter.setStretchFactor(1,1)
        self.sidebar_enabled=settings.values['sidebar']
        self.info_enabled=settings.values['info']
        self.sidebar.setVisible(self.sidebar_enabled)
        self.info_panel.setVisible(settings.values['info'])
        self.statusBar().showMessage('준비됨 · 로컬 라이브러리')
        self.cancel_button=self.button('가져오기 중단',self.cancel_import)
        self.statusBar().addPermanentWidget(self.cancel_button)
        self.cancel_button.hide()
        self.search_timer=QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(220)
        self.search_timer.timeout.connect(self.search_changed)
        self.search.textChanged.connect(lambda:self.search_timer.start())
        self.sort.currentIndexChanged.connect(self.sort_changed)
        self.slider.valueChanged.connect(self.gallery.set_size)
        self.model.changed.connect(self.loaded)
        self.model.failed.connect(self.error)
        self.gallery.doubleClicked.connect(self.open_viewer)
        self.gallery.selectionModel().currentChanged.connect(self.selection_changed)
        self.gallery.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.gallery.customContextMenuRequested.connect(self.photo_menu)
        self.import_progress.connect(lambda n:self.statusBar().showMessage(f'사진 확인 중 · {n:,}개 처리'))
        shortcut=QShortcut(QKeySequence('Ctrl+K'),self)
        shortcut.activated.connect(self.search.setFocus)
        shortcut=QShortcut(QKeySequence('Ctrl+O'),self)
        shortcut.activated.connect(lambda:self.choose_import(False))
        self.online_service=OnlineService(db,settings,WikimediaProvider(HttpClient()))
        self.online_runner=OnlineRunner(self.online_service,settings,self)
        self.online_runner.library_changed.connect(self.online_changed)
        self.online_runner.error.connect(lambda message:self.statusBar().showMessage(message,12000))
        self.restore_window()
        self.refresh_entities()
        self.navigate('all')

    def online_changed(self):
        # Keep foreground browsing stable when new verified originals are registered.
        self.refresh_entities()
        self.reload(preserve=True)

    def open_online(self):
        dialog=OnlineDialog(self.online_service,self.online_runner,self.settings,self)
        dialog.library_changed.connect(self.online_changed)
        dialog.exec()
        dialog.deleteLater()
        self.online_changed()

    def button(self,text,action):
        b=QPushButton(text)
        b.clicked.connect(action)
        return b
    def error(self,message):
        logging.error(message)
        self.statusBar().showMessage('작업 실패 · '+message,15000)
        QMessageBox.warning(self,'작업을 완료할 수 없습니다',message)
    def refresh_entities(self):
        self.entities=self.db.entities()
        self.tree.clear()
        groups=self.entities['groups']
        people=self.entities['persons']
        term=self.search.text().strip().casefold()
        for group in groups:
            members=self.db.members(group['id'])
            matching=[p for p in members if not term or term in (p['name']+' '+p['aliases']).casefold()]
            if self.section=='solo':continue
            if term and term not in (group['name']+' '+group['aliases']).casefold() and not matching:continue
            item=QTreeWidgetItem([group['name']])
            item.setData(0,Qt.ItemDataRole.UserRole,('group',group))
            self.tree.addTopLevelItem(item)
            for p in matching:
                child=QTreeWidgetItem([p['name']+('  · 숨김' if not p['gallery_target'] else '')])
                child.setData(0,Qt.ItemDataRole.UserRole,('person',p))
                item.addChild(child)
            item.setExpanded(True)
        if self.section!='groups':
            parent=QTreeWidgetItem(['솔로 · 인물'])
            self.tree.addTopLevelItem(parent)
            for p in people:
                if self.section=='solo' and not p['solo']:continue
                if term and term not in (p['name']+' '+p['aliases']).casefold():continue
                child=QTreeWidgetItem([p['name']])
                child.setData(0,Qt.ItemDataRole.UserRole,('person',p))
                parent.addChild(child)
            parent.setExpanded(True)
    def navigate(self,key):
        self.section=key
        self.scope=(key[:-1] if key=='groups' else key,None) if key in ('favorite','solo','groups') else None
        for k,b in self.nav.items():b.setChecked(k==key)
        titles=dict(all='라이브러리',latest='최신 사진',groups='그룹 컬렉션',solo='솔로 아티스트',favorite='즐겨찾기',local='로컬 라이브러리')
        self.heading.setText(titles[key])
        self.subtitle.setText('촬영일이 아닌 등록일 기준으로 정렬합니다.' if key=='latest' else '직접 모은 순간을, 선명하게.')
        self.clear_members()
        self.refresh_entities()
        self.reload()
    def clear_members(self):
        self.members_scroll.hide()
        while self.members_layout.count():
            item=self.members_layout.takeAt(0)
            if item.widget():item.widget().deleteLater()
    def select_entity(self,item,column):
        data=item.data(0,Qt.ItemDataRole.UserRole)
        if not data:return
        kind,entity=data
        self.scope=(kind,entity['id'])
        self.heading.setText(entity['name'])
        self.subtitle.setText('그룹과 연결된 사진 · 기본 표시 대상 멤버의 사진 포함' if kind=='group' else '직접 연결한 인물 사진')
        self.clear_members()
        if kind=='group':
            self.members_scroll.show()
            for p in self.db.members(entity['id']):
                b=self.button(p['name'],lambda checked=False,p=p:self.select_person(p))
                self.members_layout.addWidget(b)
            self.members_layout.addStretch()
        self.reload()
    def select_person(self,p):
        self.scope=('person',p['id'])
        self.heading.setText(p['name'])
        self.subtitle.setText('직접 연결한 인물 사진')
        self.reload()
    def reload(self,preserve=False):
        self.bookmark=None
        if preserve:
            index=self.gallery.indexAt(QPoint(12,12))
            row=self.model.row_at(index.row(),request=False) if index.isValid() else None
            if row:
                self.bookmark=(row['id'],self.gallery.visualRect(index).top(),self.selected['id'] if self.selected else None)
        self.selected=None
        self.info_name.setText('사진을 선택하세요')
        for b in (self.fav_button,self.associate_button,self.relink_button):b.setEnabled(False)
        self.model.reload(self.scope,self.search.text(),self.sort.currentData())
    def loaded(self,total):
        if self.bookmark is not None and not self.model.loading:
            identity,y_offset,selected_id=self.bookmark
            self.bookmark=None
            generation=self.model.generation
            scope,search,sort=self.model.scope,self.model.search,self.model.sort
            def locate(_):
                return (self.db.locate(identity,scope,search,sort),self.db.locate(selected_id,scope,search,sort) if selected_id else None)
            def restore(result):
                if self.model.generation!=generation:return
                position,selected=result
                if position is not None:
                    self.gallery.restore_anchor(position,y_offset,selected)
            self.meta_tasks.submit(locate,restore,self.error)
        self.count.setText(f'{total:,} PHOTOS')
        self.empty.setVisible(total==0)
        self.gallery.setVisible(total>0)
        self.gallery.schedule()
        current=self.gallery.currentIndex()
        if current.isValid() and self.model.row_at(current.row(),request=False):
            self.selection_changed(current)
    def search_changed(self):
        self.refresh_entities()
        self.reload()
    def sort_changed(self):
        self.reload()
    def toggle_sidebar(self):
        self.sidebar_enabled=not self.sidebar_enabled
        self.sidebar.setVisible(self.sidebar_enabled)
    def toggle_info(self):
        self.info_enabled=not self.info_panel.isVisible()
        self.info_panel.setVisible(self.info_enabled)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'info_enabled'):
            self.info_panel.setVisible(self.info_enabled and self.width()>=1100)
    def open_settings(self):
        if SettingsDialog(self.settings,self).exec():
            self.gallery.cache=self.settings.cache
            self.gallery.schedule()
    def add_menu(self):
        menu=QMenu(self)
        menu.addAction('그룹 추가',lambda:self.entity_dialog('group'))
        menu.addAction('인물 · 솔로 추가',lambda:self.entity_dialog('person'))
        menu.exec(self.cursor().pos())
    def entity_dialog(self,kind,existing=None):
        if EntityDialog(self.db,kind,self,existing).exec():self.refresh_entities()
    def entity_menu(self,pos):
        item=self.tree.itemAt(pos)
        data=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if not data:return
        menu=QMenu(self)
        menu.addAction('이름 · 별칭 수정',lambda:self.entity_dialog(data[0],data[1]))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
    def memberships(self):
        if LinkDialog(self.db,self).exec():
            self.refresh_entities()
            self.reload()
    def import_menu(self):
        menu=QMenu(self)
        menu.addAction('사진 파일 선택',lambda:self.choose_import(False))
        menu.addAction('폴더 선택',lambda:self.choose_import(True))
        menu.exec(self.cursor().pos())
    def choose_import(self,folder=False):
        if self.import_job:return
        if folder:
            path=QFileDialog.getExistingDirectory(self,'사진 폴더 선택')
            paths=[path] if path else []
        else:
            paths,_=QFileDialog.getOpenFileNames(self,'사진 가져오기','','이미지 (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff)')
        if not paths:return
        dialog=ImportDialog(self.db,len(paths),self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        self.start_import(paths,dialog.managed.isChecked(),dialog.person.currentData(),dialog.group.currentData())
    def start_import(self,paths,managed=False,person=None,group=None):
        self.import_button.setEnabled(False)
        self.cancel_button.show()
        cache,storage=self.settings.cache,self.settings.storage
        def done(report):
            self.import_job=None
            self.import_button.setEnabled(True)
            self.cancel_button.hide()
            self.reload(preserve=True)
            message=f"추가 {report['added']:,} · 중복 {report['duplicates']:,} · 실패 {len(report['failed']):,}"+(' · 중단됨' if report['cancelled'] else '')
            self.statusBar().showMessage(message,30000)
            if report['failed']:
                box=QMessageBox(QMessageBox.Icon.Warning,'가져오기 결과',message,parent=self)
                box.setDetailedText('\n'.join(f'{p}\n{e}' for p,e in report['failed']))
                box.exec()
        def error(message):
            self.import_job=None
            self.import_button.setEnabled(True)
            self.cancel_button.hide()
            self.error(message)
        self.import_cancel=threading.Event()
        self.import_job=self.tasks.submit(lambda cancel:import_paths(self.db,paths,cache,storage,managed,person,group,self.import_cancel,self.import_progress.emit),done,error)
    def cancel_import(self):
        if self.import_job:
            # importer returns its partial report after finishing the current file.
            self.import_cancel.set()
    def selection_changed(self,index,previous=None):
        if not index.isValid():return
        row=index.data(Qt.ItemDataRole.UserRole)
        if row is None:return
        self.selected=row
        self.info_name.setText(row['name'])
        self.fav_button.setText('★ 즐겨찾기 해제' if row['favorite'] else '☆ 즐겨찾기')
        for b in (self.fav_button,self.associate_button,self.relink_button):b.setEnabled(True)
        people,groups=self.db.associations(row['id'])
        self.info_text.setText(f"{row['width']:,} × {row['height']:,}\n{row['format']}  ·  {row['size']/1024/1024:.2f} MB\n\n인물: {', '.join(p['name'] for p in people) or '미연결'}\n그룹: {', '.join(g['name'] for g in groups) or '미연결'}\n\n{'앱 관리 원본' if row['managed'] else '외부 파일 참조'}\n{row['path']}")
        sources=self.online_service.repo.source_for_asset(row['id'])
        if sources:
            self.info_text.setText(self.info_text.text()+'\n\n'+'\n'.join(x['source_url']+'\n'+(x['license_name'] or '이용 조건 확인 필요')+'\n'+(x['attribution'] or '') for x in sources))
    def persist_favorite(self,identity,value,after=None):
        def done(_):
            for row in self.model.rows:
                if row['id']==identity:row['favorite']=value
            self.gallery.viewport().update()
            if after:after()
        def error(message):
            self.fav_button.setEnabled(self.selected is not None)
            self.error(message)
        self.meta_tasks.submit(lambda _:self.db.favorite(identity,value),done,error)
    def toggle_favorite(self):
        if not self.selected:return
        row=self.selected
        identity,value=row['id'],not row['favorite']
        self.fav_button.setEnabled(False)
        def after():
            self.fav_button.setEnabled(True)
            if self.selected and self.selected['id']==identity:
                self.fav_button.setText('★ 즐겨찾기 해제' if value else '☆ 즐겨찾기')
            if self.scope and self.scope[0]=='favorite':self.reload()
        self.persist_favorite(identity,value,after)
    def associate(self):
        if not self.selected:return
        row=self.selected
        dialog=ImportDialog(self.db,1,self)
        dialog.setWindowTitle('선택한 사진 연결')
        dialog.managed.hide()
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        person,group=dialog.person.currentData(),dialog.group.currentData()
        self.meta_tasks.submit(lambda _:self.db.register(row,person,group),lambda _:self.selection_changed(self.gallery.currentIndex()),self.error)
    def relink(self):
        if not self.selected:return
        path,_=QFileDialog.getOpenFileName(self,'동일 원본 파일 선택')
        if not path:return
        row=dict(self.selected)
        self.meta_tasks.submit(lambda _:self.db.relink(row['id'],path,row['sha256']),lambda _:self.reload(),self.error)
    def photo_menu(self,pos):
        index=self.gallery.indexAt(pos)
        if not index.isValid():return
        self.gallery.setCurrentIndex(index)
        menu=QMenu(self)
        menu.addAction('뷰어에서 열기',lambda:self.open_viewer(index))
        menu.addAction('즐겨찾기 전환',self.toggle_favorite)
        menu.addAction('그룹 · 인물 연결',self.associate)
        menu.addAction('원본 다시 연결',self.relink)
        menu.exec(self.gallery.viewport().mapToGlobal(pos))
    def open_viewer(self,index):
        if not index.isValid() or self.model.row_at(index.row()) is None:return
        scroll=self.gallery.verticalScrollBar().value()
        scope,search,sort=self.model.scope,self.model.search,self.model.sort
        base=(index.row()//self.model.PAGE_SIZE)*self.model.PAGE_SIZE
        seed=[dict(r) for r in self.model.pages[base//self.model.PAGE_SIZE]]
        viewer=Viewer(seed,index.row()-base,self,self.settings.values['viewer_mode'],lambda offset:self.db.query(scope,search,sort,offset),self.model.total,base_index=base)
        def favorite(identity,value):
            self.persist_favorite(identity,value)
        viewer.favorite_changed.connect(favorite)
        viewer.exec()
        viewer.deleteLater()
        self.gallery.verticalScrollBar().setValue(scroll)
        self.gallery.viewport().update()
        self.selection_changed(index)
    def restore_window(self):
        geometry=self.settings.values.get('geometry')
        if isinstance(geometry,str):
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
        screen=QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        available=screen.availableGeometry()
        self.resize(min(self.width(),available.width()),min(self.height(),available.height()))
        self.move(max(available.left(),min(self.x(),available.right()-self.width()+1)),max(available.top(),min(self.y(),available.bottom()-self.height()+1)))
        sizes=self.settings.values.get('panels')
        if isinstance(sizes,list) and len(sizes)==3 and all(isinstance(n,int) and n>=0 for n in sizes):self.splitter.setSizes(sizes)
    def closeEvent(self,event):
        if self.import_job:
            self.import_cancel.set()
            self.statusBar().showMessage('현재 파일 작업을 마친 뒤 종료합니다.')
        self.search_timer.stop()
        self.online_runner.stop()
        self.gallery.close_tasks()
        self.model.close_tasks()
        self.tasks.stop()
        self.meta_tasks.stop()
        self.settings.values.update(geometry=bytes(self.saveGeometry().toBase64()).decode(),panels=self.splitter.sizes(),sidebar=self.sidebar_enabled,info=self.info_enabled,thumbnail_size=self.slider.value(),sort=self.sort.currentData())
        try:self.settings.save()
        except Exception as error:
            self.error('설정을 저장하지 못했습니다: '+str(error))
        event.accept()
