import threading
from app.core import Settings
from app.online_service import OnlineService
from app.search_service import SearchService
from app.providers.kpopping_browser import KpoppingBrowserProvider,OBSERVED_PAGES
from app import automatic_sources
from test_sources import EVIDENCE


def test_new_and_old_installations_default_to_automatic(tmp_path):
    root=tmp_path/'user';root.mkdir();(root/'settings.json').write_text('{"kpopping_show_browser":true}')
    assert Settings(root).values['automatic_search'] is True


def test_supported_public_sources_need_no_per_site_checkbox(db,settings,monkeypatch):
    settings.values.update(automatic_search=True,kpopping_browser_enabled=False,dbkpop_search_enabled=False)
    monkeypatch.setattr(automatic_sources.platform,'system',lambda:'Windows')
    def missing(_):raise RuntimeError('No credentials')
    monkeypatch.setattr(automatic_sources.naver_credentials,'load',missing)
    search=SearchService(OnlineService(db,settings,None))
    assert set(search.providers)=={'dbkpop-search','kpopping-browser'}
    repo=search.online.web.repo
    assert repo.allowed('dbkpop-search','thumbnail') and repo.allowed('kpopping-browser','original_download')
    assert not repo.allowed('kpopping-browser','ai_restore_input')
    states={item['source']:item['state'] for item in search.source_status}
    assert states['naver-image-search']=='requires_api_credentials'
    assert states['instagram']=='adapter_not_connected'


def test_saved_api_credentials_join_without_a_source_checkbox(db,settings,monkeypatch):
    settings.values['automatic_search']=True
    monkeypatch.setattr(automatic_sources.naver_credentials,'load',lambda _:('fixture-id','fixture-secret'))
    search=SearchService(OnlineService(db,settings,None))
    assert 'naver-image-search' in search.providers


def test_old_visible_mode_is_ignored_for_automatic_search(db,settings,monkeypatch):
    settings.values.update(automatic_search=True,kpopping_show_browser=True)
    monkeypatch.setattr(automatic_sources.platform,'system',lambda:'Windows')
    online=OnlineService(db,settings,None);search=SearchService(online)
    provider=search.providers['kpopping-browser'];requests=[]
    def worker(request,cancel):
        requests.append(request)
        return dict(page_url=OBSERVED_PAGES['장원영'],title='Fixture',items=[],next_batch=None,filter_verified=True)
    monkeypatch.setattr(provider,'_worker',worker)
    provider.search_images('장원영',None,threading.Event())
    assert requests[0]['show_browser'] is False


def test_existing_photo_denial_survives_automatic_source_selection(db,settings,monkeypatch):
    settings.values['automatic_search']=True
    monkeypatch.setattr(automatic_sources.platform,'system',lambda:'Windows')
    online=OnlineService(db,settings,None)
    search=SearchService(online)
    online.web.repo.grant('kpopping-browser','original_download','denied',EVIDENCE,'web:fixture-photo')
    SearchService(online)
    assert not online.web.repo.allowed('kpopping-browser','original_download','web:fixture-photo')


def test_first_batch_queries_all_selected_sources_with_two_workers(db,settings):
    from app.analysis_filters import AnalysisFilter
    lock=threading.Lock();barrier=threading.Barrier(2)
    active=0;maximum=0;calls=[]
    class Provider:
        def __init__(self,name):self.name=name
        def search_images(self,*args):
            nonlocal active,maximum
            with lock:
                active+=1;maximum=max(maximum,active);calls.append(self.name);ordinal=len(calls)
            if ordinal<=2:barrier.wait(timeout=3)
            with lock:active-=1
            return [],None
    search=SearchService(OnlineService(db,settings,None),[Provider(str(i)) for i in range(5)])
    identity=search.start('IVE',AnalysisFilter())
    result=search.page(identity,threading.Event())
    assert set(calls)==set(map(str,range(5))) and maximum==2
    assert result['state']=='exhausted'


def test_automatic_dialog_does_not_require_individual_checks_or_references(qtbot,db,settings,monkeypatch):
    from PySide6.QtWidgets import QDialog,QLineEdit,QCheckBox
    from app.main_window import MainWindow
    settings.values.update(automatic_search=True,kpopping_show_browser=True)
    window=MainWindow(db,settings);qtbot.addWidget(window);window.online_runner.timer.stop()
    def accept(dialog):
        automatic=next(box for box in dialog.findChildren(QCheckBox) if box.text().startswith('지원하는 출처'))
        assert automatic.isChecked()
        for field in dialog.findChildren(QLineEdit):field.clear()
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,'exec',accept)
    window.configure_browser_search()
    assert settings.values['automatic_search'] and not settings.values['kpopping_show_browser']
    assert 'dbkpop-search' in window.internet_search.providers
    assert Settings(settings.root).values['automatic_search']
    window.close()
