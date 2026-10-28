import logging
import threading
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QByteArray,Signal,QPoint,QUrl
from PySide6.QtGui import QShortcut,QKeySequence,QGuiApplication,QDesktopServices
from PySide6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QLineEdit,QComboBox,QFrame,QSplitter,QTreeWidget,QTreeWidgetItem,QSlider,QFileDialog,QMessageBox,QDialog,QMenu,QScrollArea,QSizePolicy,QCheckBox)
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
from .related_dialog import RelatedDialog
from .related import RelatedRepository
from .library import LibraryRepository,LibraryService,remote
from .remote_links import RemoteLinksDialog
from .analysis_filters import AnalysisFilter
from .analysis_dialog import AnalysisDialog
from .page_discovery import clean_url

class MainWindow(QMainWindow):
    import_progress = Signal(int)
    search_results_ready = Signal(str)
    def __init__(self,db,settings):
        super().__init__()
        self.db,self.settings=db,settings
        self.setWindowTitle('K-IDOL ARCHIVE · 검색 개편 1단계 실험')
        self.resize(1440,920)
        self.setMinimumSize(820,560)
        self.scope=None
        self.section='all'
        self.selected=None
        try:self.analysis_filter=AnalysisFilter(**settings.values.get('analysis_filter',{}))
        except (ValueError,TypeError):self.analysis_filter=AnalysisFilter()
        if settings.values.get('sort')=='color_share' and not self.analysis_filter.color:settings.values['sort']='newest'
        self.analysis_dialog=None
        self.search_session=None;self.search_flight=None;self.search_auto_ready=False
        self.search_tasks=Tasks(self,1)
        from .upscale_service import UpscaleService
        self.upscale_service=UpscaleService(db,settings);self.upscale_service.recover();self.upscale_dialogs=[]
        self.library=LibraryRepository(db)
        self.gallery_version=-1
        self.refresh_force=False
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
        self.web_search_button=self.button('인터넷 검색',self.start_internet_search);top.addWidget(self.web_search_button)
        top.addWidget(self.button('검색 출처',self.configure_browser_search))
        self.search.returnPressed.connect(self.start_internet_search)
        self.search_composition=QComboBox()
        for label,value in [('전체 구도',None),('얼굴','face_close'),('얼굴·어깨','face_shoulders'),('상반신','upper_body'),('허리 위','waist_up'),('무릎 위','knees_up'),('전신','full_body')]:self.search_composition.addItem(label,value)
        top.addWidget(self.search_composition)
        self.search_unknown=QCheckBox('미확정 포함');self.search_unknown.setChecked(True);top.addWidget(self.search_unknown)
        self.next_search=self.button('더 검색',self.next_internet_page);top.addWidget(self.next_search);self.next_search.setEnabled(False)
        self.analysis_toggle=self.button('필터 · 분석',self.toggle_analysis)
        top.addWidget(self.analysis_toggle)
        self.info_toggle=self.button('정보 패널',self.toggle_info)
        top.addWidget(self.info_toggle)
        self.sync_label=QLabel('준비됨');self.sync_label.setObjectName('muted');top.addWidget(self.sync_label)
        top.addWidget(self.button('온라인',self.open_online))
        top.addWidget(self.button('설정',self.open_settings))
        outer.addWidget(toolbar)
        search_controls=QWidget();search_row=QHBoxLayout(search_controls);search_row.setContentsMargins(20,4,20,4)
        self.search_auto=QCheckBox('스크롤 시 더 검색');self.search_auto.setChecked(True)
        for widget in (self.search_composition,self.search_unknown,self.next_search,self.search_auto):
            top.removeWidget(widget);search_row.addWidget(widget)
        search_row.addStretch();outer.addWidget(search_controls)
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
        label=QLabel('SEARCH · 1단계 실험\n로컬 우선 · 원본 보존')
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
        for label,value in [('최신 등록순','newest'),('검색 발견순','discovery'),('오래된 등록순','oldest'),('파일명순','name'),('해상도순','resolution'),('색상 점유율순','color_share')]:self.sort.addItem(label,value)
        self.sort.setCurrentIndex(self.sort.findData(settings.values['sort']))
        refresh=self.button('↻',lambda:self.reload(preserve=True))
        refresh.setToolTip('라이브러리 새로고침')
        tools.addWidget(refresh)
        self.library_mode=QComboBox()
        for label,value in [('로컬 + 온라인','all'),('로컬만','local'),('온라인 후보만','online')]:self.library_mode.addItem(label,value)
        self.library_mode.setCurrentIndex(max(0,self.library_mode.findData(settings.values['library_mode'])))
        self.library_mode.setToolTip('온라인은 메타데이터 후보입니다. 표시 권한이 확인된 썸네일만 불러옵니다.')
        tools.addWidget(self.library_mode)
        self.source_filter=QComboBox()
        self.source_filter.addItem('전체 출처',None)
        for name,category in [('공식 사진','official'),('SNS','social'),('언론사','press'),('팬 촬영','fan'),('사용자 등록','user')]:self.source_filter.addItem(name,'category:'+category)
        self.source_filter.setToolTip('출처별 필터 · 연결되지 않은 출처에는 결과가 없습니다.')
        tools.addWidget(self.source_filter)
        tools.addWidget(self.sort)
        self.slider=QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(128,384)
        self.slider.setValue(settings.values['thumbnail_size'])
        self.slider.setFixedWidth(105)
        self.slider.setToolTip('썸네일 크기')
        tools.addWidget(self.slider)
        content.addLayout(tools)
        self.model=GalleryModel(LibraryRepository(db,settings.values['library_mode'],self.analysis_filter),self)
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
        self.info_panel=QScrollArea()
        self.info_panel.setWidgetResizable(True);self.info_panel.setFrameShape(QFrame.Shape.NoFrame)
        self.info_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        info_content=QWidget();self.info_panel.setWidget(info_content)
        self.info_panel.setMinimumWidth(210)
        info=QVBoxLayout(info_content)
        info.setContentsMargins(20,28,20,20)
        label=QLabel('PHOTO DETAILS')
        label.setObjectName('eyebrow')
        info.addWidget(label)
        info.addSpacing(16)
        self.info_name=QLabel('사진을 선택하세요')
        self.info_name.setObjectName('infoTitle')
        self.info_name.setTextFormat(Qt.TextFormat.PlainText)
        self.info_name.setWordWrap(True)
        self.info_name.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        info.addWidget(self.info_name)
        self.info_text=QLabel('해상도, 보관 위치와 연결 정보를\n여기에서 확인할 수 있습니다.')
        self.info_text.setObjectName('muted')
        self.info_text.setTextFormat(Qt.TextFormat.PlainText)
        self.info_text.setWordWrap(True)
        self.info_text.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.info_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(self.info_text)
        self.fav_button=self.button('☆ 즐겨찾기',self.toggle_favorite)
        info.addWidget(self.fav_button)
        self.associate_button=self.button('그룹 · 인물 연결',self.associate)
        info.addWidget(self.associate_button)
        self.related_button=self.button('연관 사진 찾기',self.open_related)
        info.addWidget(self.related_button)
        self.upscale_button=self.button('AI 업스케일 · 비교',self.open_upscale);info.addWidget(self.upscale_button)
        self.relink_button=self.button('원본 다시 연결',self.relink)
        info.addWidget(self.relink_button)
        self.preserve_button=self.button('파일 영구 보관',self.preserve_selected)
        self.source_button=self.button('출처 페이지 열기',self.open_selected_source)
        info.addWidget(self.preserve_button);info.addWidget(self.source_button)
        self.preserve_button.setEnabled(False);self.source_button.setEnabled(False)
        for b in (self.fav_button,self.associate_button,self.relink_button,self.related_button,self.upscale_button):b.setEnabled(False)
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
        self.source_filter.currentIndexChanged.connect(lambda:self.reload())
        self.library_mode.currentIndexChanged.connect(self.library_mode_changed)
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
        self.library_service=LibraryService(self.online_service)
        self.gallery.thumbnail_loader=self.library_service.preview
        self.remote_timer=QTimer(self);self.remote_timer.setSingleShot(True);self.remote_timer.setInterval(650);self.remote_timer.timeout.connect(self.check_remote_refresh)
        from .search_service import SearchService
        self.internet_search=SearchService(self.online_service)
        self.search_results_ready.connect(self.partial_search_results)
        self.search_scroll_timer=QTimer(self);self.search_scroll_timer.setSingleShot(True);self.search_scroll_timer.setInterval(120)
        self.search_scroll_timer.timeout.connect(self.search_near_bottom)
        self.gallery.more_requested.connect(lambda:self.search_scroll_timer.start())
        self.gallery.verticalScrollBar().sliderMoved.connect(lambda _:self.search_scroll_timer.start())
        self.gallery.verticalScrollBar().actionTriggered.connect(lambda _:self.search_scroll_timer.start())
        self.online_runner=OnlineRunner(self.online_service,settings,self)
        self.gallery.verticalScrollBar().valueChanged.connect(lambda _:self.online_runner.defer())
        self.online_runner.library_changed.connect(self.online_changed)
        self.online_runner.changed.connect(self.remote_changed)
        self.online_runner.state_changed.connect(self.sync_label.setText)
        self.online_runner.error.connect(lambda message:self.statusBar().showMessage(message,12000))
        self.refresh_source_filters()
        self.restore_window()
        self.refresh_entities()
        self.navigate('all')

    def selected_source_filter(self):
        value=self.source_filter.currentData()
        return tuple(value.split(':',1)) if value else None

    def refresh_source_filters(self):
        current=self.source_filter.currentData()
        self.source_filter.blockSignals(True)
        while self.source_filter.count()>6:self.source_filter.removeItem(6)
        for source in self.online_service.web.repo.profiles():
            if source['adapter'] not in ('candidate','local'):
                self.source_filter.addItem(source['name'],'site:'+source['id'])
        index=self.source_filter.findData(current)
        self.source_filter.setCurrentIndex(max(0,index))
        self.source_filter.blockSignals(False)

    def toggle_analysis(self):
        if self.analysis_dialog is None:
            self.analysis_dialog=AnalysisDialog(self.online_service,self.online_runner,self.analysis_filter,self)
            self.analysis_dialog.applied.connect(self.apply_analysis_filters)
            self.analysis_dialog.changed.connect(self.remote_changed)
        if self.analysis_dialog.isVisible():self.analysis_dialog.hide()
        else:
            self.analysis_dialog.show()
            self.analysis_dialog.set_entry(self.selected['id'] if self.selected else None)
            self.analysis_dialog.raise_()

    def apply_analysis_filters(self,filters):
        self.analysis_filter=filters;self.settings.values['analysis_filter']=filters.dictionary()
        self.search_composition.setCurrentIndex(max(0,self.search_composition.findData(filters.composition)));self.search_unknown.setChecked(filters.include_unknown)
        self.analysis_toggle.setText('필터 · 적용 중' if filters.active else '필터 · 분석')
        if self.sort.currentData()=='color_share' and not filters.color:self.sort.setCurrentIndex(0)
        self.reload()

    def library_mode_changed(self):
        self.settings.values['library_mode']=self.library_mode.currentData()
        self.reload()

    def remote_changed(self):
        if (self.library_mode.currentData()!='local' or self.analysis_filter.active) and not self.remote_timer.isActive():self.remote_timer.start()

    def check_remote_refresh(self):
        force=self.refresh_force;self.refresh_force=False
        def done(version):
            if force or version!=self.gallery_version:
                self.gallery_version=version;self.reload(preserve=True)
        self.meta_tasks.submit(lambda _:self.library.version(),done,self.error,key='gallery-revision')

    def online_changed(self):
        self.refresh_entities();self.refresh_source_filters()
        self.refresh_force=True
        if not self.remote_timer.isActive():self.remote_timer.start()

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
        self.search_session=None;self.next_search.setEnabled(False)
        if self.search_flight:self.search_flight.cancelled.set()
        self.section=key
        self.scope=(key[:-1] if key=='groups' else key,None) if key in ('favorite','solo','groups','local') else None
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
        self.search_session=None;self.next_search.setEnabled(False)
        if self.search_flight:self.search_flight.cancelled.set()
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
        self.search_session=None;self.next_search.setEnabled(False)
        if self.search_flight:self.search_flight.cancelled.set()
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
        self.info_text.setText('사진을 선택하면 보관 상태와 출처 정보를 확인할 수 있습니다.')
        for b in (self.fav_button,self.associate_button,self.relink_button,self.related_button,self.upscale_button):b.setEnabled(False)
        self.preserve_button.setEnabled(False);self.source_button.setEnabled(False)
        if self.analysis_dialog is not None and self.analysis_dialog.isVisible():self.analysis_dialog.set_entry(None)
        self.empty.setText('조건에 맞는 사진이 없습니다\n\n색상/구도 분석 완료와 권한을 확인하거나 미분석 포함·필터 초기화를 사용하세요.' if self.analysis_filter.active else '아직 사진이 없습니다\n\n사진을 가져오거나 그룹과 인물을 먼저 등록해 보세요.')
        if self.scope and self.scope[0]=='search':self.empty.setText('아직 표시할 검색 후보가 없습니다\n\n검색 진행·출처 상태를 확인하세요. 다른 이름·별칭이나 미확정 포함 조건으로 검색할 수 있습니다.')
        self.model.db=LibraryRepository(self.db,self.library_mode.currentData(),self.analysis_filter)
        self.model.reload(self.scope,'' if self.scope and self.scope[0]=='search' else self.search.text(),self.sort.currentData(),source_filter=self.selected_source_filter())
    def loaded(self,total):
        if self.bookmark is not None and not self.model.loading:
            identity,y_offset,selected_id=self.bookmark
            self.bookmark=None
            generation=self.model.generation
            scope,search,sort=self.model.scope,self.model.search,self.model.sort
            source_filter=self.model.source_filter
            repository=self.model.db
            def locate(_):
                return (repository.locate(identity,scope,search,sort,source_filter),repository.locate(selected_id,scope,search,sort,source_filter) if selected_id else None)
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
    def configure_browser_search(self):
        from PySide6.QtWidgets import QFormLayout,QDialogButtonBox
        from .providers.kpopping_browser import KpoppingBrowserProvider
        from .providers.flickr_feed import FlickrFeedProvider
        dialog=QDialog(self);dialog.setWindowTitle('검색 출처 · 실험');form=QFormLayout(dialog)
        note=QLabel('출처별 공개 검색·미리보기 연결입니다. 실제 다중 출처 동작은 아직 검증되지 않았습니다.\nKpopping: Windows Edge/Chrome 필요. 장원영 목록 경로만 사용자 관측으로 확인했습니다.\nFlickr: API 키 없는 공개 태그 피드, 검색어별 최근 최대 20개. 전체 이미지 검색·과거 페이지 탐색은 지원하지 않습니다.\n검색 결과의 인물·구도·원본은 미확정이며 원본 다운로드·분석·AI 권한을 부여하지 않습니다.');note.setWordWrap(True);form.addRow(note)
        fields=[]
        for provider_type,key,label in ((KpoppingBrowserProvider,'kpopping_browser_enabled','Kpopping 브라우저'),(FlickrFeedProvider,'flickr_feed_enabled','Flickr 공개 태그 피드')):
            consent=QCheckBox(label+' · 이용 조건을 확인한 검색·미리보기만 활성화')
            consent.setChecked(bool(self.settings.values.get(key,False)));form.addRow(consent)
            reference=QLineEdit();reference.setPlaceholderText('확인한 이용 조건 URL 또는 문서 식별자')
            existing=self.internet_search.providers.get(provider_type.name)
            if existing:reference.setText(self.online_service.web.repo.permissions(existing.name)['metadata_search']['evidence'].get('reference',''))
            form.addRow('확인 근거',reference);fields.append((provider_type,key,consent,reference))
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        if any(consent.isChecked() and not reference.text().strip() for _,_,consent,reference in fields):self.statusBar().showMessage('활성화할 출처별 이용 조건 확인 근거가 필요합니다.');return
        if self.search_flight:self.search_flight.cancelled.set()
        self.search_session=None;self.search_auto_ready=False;self.next_search.setEnabled(False);self.search_scroll_timer.stop()
        for provider_type,key,consent,reference in fields:
            enabled=consent.isChecked();provider=provider_type(self.online_service)
            if enabled:
                evidence=dict(reference=reference.text().strip(),note='User-reviewed public metadata search and provided preview display only. Not verified original, identity or AI rights.')
                self.online_service.web.repo.grant_many(provider.name,{'metadata_search':'allowed','thumbnail':'allowed'},evidence,enabled=True)
                self.internet_search.providers[provider.name]=provider
            else:
                self.online_service.web.repo.enable(provider.name,False);self.internet_search.providers.pop(provider.name,None)
            self.settings.values[key]=enabled
        self.settings.save();self.refresh_source_filters()
        self.statusBar().showMessage('검색 출처를 저장했습니다. 검색어 입력 후 인터넷 검색을 누르세요.',10000)

    def partial_search_results(self,session):
        if session!=self.search_session:return
        self.refresh_force=True;self.remote_changed()

    def start_internet_search(self):
        from dataclasses import replace
        try:
            filters=replace(self.analysis_filter,composition=self.search_composition.currentData(),include_unknown=self.search_unknown.isChecked())
            session=self.internet_search.start(self.search.text(),filters)
        except ValueError as error:self.statusBar().showMessage(str(error));return
        if self.search_flight:self.search_flight.cancelled.set()
        self.search_session=session;self.analysis_filter=filters;self.scope=('search',session);self.section='search'
        self.search_auto_ready=False;self.search_scroll_timer.stop()
        self.sort.blockSignals(True);self.sort.setCurrentIndex(self.sort.findData('discovery'));self.sort.blockSignals(False)
        self.library_mode.setCurrentIndex(self.library_mode.findData('all'))
        self.heading.setText('온라인 검색 · '+self.search.text().strip());self.subtitle.setText('출처 검색 후보 · 인물/구도/색상 미확정은 일치로 간주하지 않습니다.')
        self.reload();self.next_internet_page()

    def next_internet_page(self):
        if not self.search_session or (self.search_flight and not self.search_flight.cancelled.is_set()):return
        session=self.search_session;self.search_auto_ready=False;self.next_search.setEnabled(False);self.sync_label.setText('검색 중')
        def done(result):
            self.search_flight=None
            if self.search_session!=session:return
            self.reload(preserve=True);self.next_search.setEnabled(result['state'] not in ('exhausted','blocked'))
            self.search_auto_ready=result['state']=='ready' and result['added']>0
            if result['state']=='blocked':status='출처 접근 불가 · 확인 필요'
            elif result.get('error'):status='일부 출처 실패 · '+('다음 검색 가능' if result['state']=='ready' else '남은 검색 경로 없음')
            elif result['state']=='exhausted':status='현재 검색 경로 끝 · 사이트 전체 탐색 완료는 아님'
            elif not result['added']:status='새 후보 없음 · 더 검색으로 재시도'
            else:status='다음 결과 있음 · 스크롤 시 추가 검색'
            self.sync_label.setText(status)
        def failed(message):
            self.search_flight=None
            if self.search_session!=session:return
            self.search_auto_ready=False
            self.sync_label.setText('검색 불가 · 확인 필요')
            self.next_search.setEnabled(self.internet_search.session(session)['state']!='blocked')
            self.empty.setText('온라인 검색을 완료하지 못했습니다\n\n'+message);self.statusBar().showMessage(message,20000)
        self.search_flight=self.search_tasks.submit(lambda cancel:self.internet_search.page(session,cancel,self.search_results_ready.emit),done,failed,key='internet-search')

    def search_near_bottom(self):
        # Only user scroll gestures arm this timer. Model resets/anchor restoration
        # must never produce an unbounded network request loop.
        if not self.search_auto.isChecked() or not self.search_auto_ready or not self.search_session:return
        if self.scope!=('search',self.search_session) or self.model.loading or self.bookmark is not None:return
        bar=self.gallery.verticalScrollBar()
        if bar.maximum()-bar.value()<=max(200,self.gallery.viewport().height()):self.next_internet_page()

    def search_changed(self):
        self.refresh_entities()
        self.reload()
    def sort_changed(self):
        if self.sort.currentData()=='color_share' and not self.analysis_filter.color:
            self.statusBar().showMessage('색상 필터를 먼저 선택하세요.',6000);self.sort.setCurrentIndex(0);return
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
        if EntityDialog(self.db,kind,self,existing).exec():self.refresh_entities();self.reload(preserve=True)
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
        if self.analysis_dialog is not None and self.analysis_dialog.isVisible():self.analysis_dialog.set_entry(row['id'])
        self.info_name.setText(row['name'])
        self.fav_button.setText('★ 즐겨찾기 해제' if row['favorite'] else '☆ 즐겨찾기')
        for b in (self.fav_button,self.associate_button,self.relink_button,self.related_button):b.setEnabled(True)
        self.preserve_button.setEnabled(remote(row));self.source_button.setEnabled(bool(row.get('page_url')))
        self.relink_button.setEnabled(not remote(row));self.related_button.setEnabled(not remote(row));self.upscale_button.setEnabled(not remote(row))
        if remote(row):
            self.info_text.setText('출처와 이용 조건을 확인하는 중…')
            def done(result):
                if not self.selected or self.selected['id']!=row['id']:return
                entry,people,groups=result
                measured=f"캐시 실측 {entry['width']:,} × {entry['height']:,}" if entry['width'] and entry['height'] else f"미실측 · 제공처 선언 {entry['provided_width'] or '?'} × {entry['provided_height'] or '?'}"
                self.info_text.setText('온라인 후보 · 영구 보관 전\n'+measured+'\n\n등장 확인: '+(', '.join(p['name'] for p in people) or '미확인')+'\n그룹 연결: '+(', '.join(g['name'] for g in groups) or '미연결')+'\n\n'+entry['page_url']+'\n'+entry['image_url']+'\n\n보기 캐시와 영구 보관은 별개입니다. 제공 파일 주소와 출처·사진별 다운로드 권한을 확인한 뒤 파일을 보관합니다. 미리보기만 있으면 원본으로 보관하지 않습니다.')
            self.meta_tasks.submit(lambda _:self.library.details(row['id']),done,self.error,key='remote-details')
            return
        people,groups=self.db.associations(row['id'])
        self.info_text.setText(f"{row['width']:,} × {row['height']:,}\n{row['format']}  ·  {row['size']/1024/1024:.2f} MB\n\n인물: {', '.join(p['name'] for p in people) or '미연결'}\n그룹: {', '.join(g['name'] for g in groups) or '미연결'}\n\n{'앱 관리 원본' if row['managed'] else '외부 파일 참조'}\n{row['path']}")
        with self.db.connect() as conn:
            web_sources=[dict(r) for r in conn.execute("SELECT p.name,s.page_url,s.image_url,s.license_url,s.attribution FROM asset_sources s JOIN source_profiles p ON p.id=s.source_id WHERE s.asset_id=? AND s.item_id<>''",(row['id'],))]
        if web_sources:self.info_text.setText(self.info_text.text()+'\n\n'+'\n'.join(x['name']+'\n'+x['page_url']+'\n'+x['image_url']+'\n'+x['license_url']+'\n'+x['attribution'] for x in web_sources))
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
        self.meta_tasks.submit(lambda _:self.library.favorite(identity,value),done,error)
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
        if remote(row):
            def edit(result):
                entities,associations=result
                dialog=RemoteLinksDialog(entities,*associations,self)
                if dialog.exec()!=QDialog.DialogCode.Accepted:return
                people=dialog.selected(dialog.people);groups=dialog.selected(dialog.groups)
                self.meta_tasks.submit(lambda _:self.library.associate(row['id'],people,groups),lambda _:self.reload(preserve=True),self.error)
            self.meta_tasks.submit(lambda _:(self.db.entities(),self.library.associations(row['id'])),edit,self.error)
            return
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
        if self.selected is None:return
        menu=QMenu(self)
        menu.addAction('뷰어에서 열기',lambda:self.open_viewer(index))
        menu.addAction('이 사진과 연관된 사진 찾기',self.open_related).setEnabled(not remote(self.selected))
        if remote(self.selected):
            menu.addAction('파일 영구 보관',self.preserve_selected)
            menu.addAction('출처 페이지 열기',self.open_selected_source)
        menu.addAction('즐겨찾기 전환',self.toggle_favorite)
        menu.addAction('그룹 · 인물 연결',self.associate)
        menu.addAction('원본 다시 연결',self.relink).setEnabled(not remote(self.selected))
        menu.exec(self.gallery.viewport().mapToGlobal(pos))
    def open_related(self,identity=None):
        if not isinstance(identity,str):identity=self.selected['id'] if self.selected else None
        if not identity:return
        if self.library.entry(identity)['kind']!='asset':
            self.statusBar().showMessage('연관 사진 탐색은 원본 보관 후 사용할 수 있습니다.',6000);return
        dialog=RelatedDialog(self.db,self.settings,identity,self)
        with self.online_runner.foreground():dialog.exec()
        dialog.deleteLater()
        self.reload(preserve=True)

    def preserve_selected(self):
        if not self.selected or not remote(self.selected):return
        row=dict(self.selected);self.preserve_button.setEnabled(False)
        def done(identity):
            self.online_runner.request(identity)
            self.statusBar().showMessage('파일 보관을 요청했습니다. 진행 상황과 제공 파일 구분은 온라인 → 작업 상태에서 확인하세요.',8000)
            self.preserve_button.setEnabled(bool(self.selected and remote(self.selected)))
        self.meta_tasks.submit(lambda _:self.library_service.preserve(row),done,self.error)

    def open_selected_source(self):
        if not self.selected:return
        url=self.selected.get('page_url','')
        if clean_url(url,url):QDesktopServices.openUrl(QUrl(url))

    def open_upscale(self,identity=None,parent=None):
        if not isinstance(identity,str):identity=self.selected['id'] if self.selected else None
        if not identity:return
        if remote(identity):self.error('온라인 사진은 원본 영구 보관과 권한 확인 후 복원할 수 있습니다.');return
        from .upscale_dialog import UpscaleDialog
        for dialog in self.upscale_dialogs:
            if not dialog.closed and dialog.identity==identity:dialog.show();dialog.raise_();return
        dialog=UpscaleDialog(self.upscale_service,identity,parent or self)
        self.upscale_dialogs.append(dialog);dialog.show()

    def open_viewer(self,index):
        if not index.isValid() or self.model.row_at(index.row()) is None:return
        scroll=self.gallery.verticalScrollBar().value()
        scope,search,sort=self.model.scope,self.model.search,self.model.sort
        source_filter=self.model.source_filter
        base=(index.row()//self.model.PAGE_SIZE)*self.model.PAGE_SIZE
        seed=[dict(r) for r in self.model.pages[base//self.model.PAGE_SIZE]]
        repository=self.model.db
        viewer=Viewer(seed,index.row()-base,self,self.settings.values['viewer_mode'],lambda offset:repository.query(scope,search,sort,offset,source_filter=source_filter),self.model.total,base_index=base,related_repo=RelatedRepository(self.db),related_cache=self.settings.cache,row_resolver=self.library_service.original)
        def favorite(identity,value):
            self.persist_favorite(identity,value)
        viewer.favorite_changed.connect(favorite)
        viewer.related_requested.connect(self.open_related)
        viewer.upscale_requested.connect(lambda identity:self.open_upscale(identity,viewer))
        with self.online_runner.foreground():viewer.exec()
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
        if any(not d.closed and d.busy for d in self.upscale_dialogs):
            for dialog in self.upscale_dialogs:
                if not dialog.closed:dialog.close()
            event.ignore();QTimer.singleShot(100,self.close);return
        for dialog in self.upscale_dialogs:
            if not dialog.closed:dialog.close()
        if self.analysis_dialog is not None and self.analysis_dialog.saving:
            self.statusBar().showMessage('분석 확인 기록을 저장한 뒤 종료합니다.');event.ignore();QTimer.singleShot(80,self.close);return
        if self.analysis_dialog is not None:self.analysis_dialog.shutdown()
        if self.import_job:
            self.import_cancel.set()
            self.statusBar().showMessage('현재 파일 작업을 마친 뒤 종료합니다.')
        self.search_tasks.stop()
        self.search_timer.stop()
        self.remote_timer.stop()
        self.search_scroll_timer.stop()
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
