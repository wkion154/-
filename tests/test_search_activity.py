import threading
from PySide6.QtCore import Qt
from app.search_activity import SearchActivity
from app.providers.http import RemoteError
from test_search import searches


def test_single_source_progress_is_emitted_after_results_are_committed(searches):
    search,provider=searches;events=[]
    from app.analysis_filters import AnalysisFilter
    from app.library import LibraryRepository
    identity=search.start('IVE',AnalysisFilter())
    def progress(event):
        events.append(event)
        if event['state']=='complete':assert LibraryRepository(search.db).query(scope=('search',identity))[1]==1
    search.page(identity,threading.Event(),on_progress=progress)
    assert [event['state'] for event in events]==['queued','running','complete']
    assert events[-1]['added']==1


def test_panel_shows_elapsed_waiting_failure_and_preview_states(qtbot):
    panel=SearchActivity();qtbot.addWidget(panel)
    panel.begin('one',{'fast':'사진 출처','slow':'응답 출처'})
    panel.update_event('one',dict(source='slow',state='running'))
    panel.sources['slow']['started']-=31;panel.tick()
    assert '응답 지연' in panel.sources['slow']['label'].text()
    panel.update_event('one',dict(source='fast',state='complete',added=12))
    assert '12' in panel.heading.text()
    panel.update_event('one',dict(source='slow',state='failed',error='HTTP 403'))
    assert '403' in panel.sources['slow']['label'].text()
    panel.preview_progress(3,9,0);panel.finish('one','일부 출처 실패')
    assert not panel.active and not panel.stop.isEnabled() and not panel.bar.isHidden()
    panel.preview_progress(12,0,0)
    assert panel.bar.isHidden()
    panel.update_event('old',dict(source='fast',state='complete',added=999))
    assert panel.sources['fast']['count']==12


def test_fast_results_are_visible_while_another_source_waits_and_stop_keeps_them(qtbot,searches):
    from app.main_window import MainWindow
    search,provider=searches;started=threading.Event()
    class Slow:
        name='slow-fixture'
        def search_images(self,query,cursor,cancelled):
            started.set();cancelled.wait(5);return [],None
    window=MainWindow(search.db,search.online.settings);qtbot.addWidget(window);window.online_runner.timer.stop()
    window.internet_search.providers={provider.name:provider,Slow.name:Slow()}
    window.show();window.search.setText('IVE');window.start_internet_search()
    qtbot.waitUntil(lambda:started.is_set() and window.search_activity.sources[provider.name]['count']==1)
    assert window.search_activity.active and window.search_flight is not None
    qtbot.mouseClick(window.search_activity.stop,Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda:not window.model.loading and window.model.total==1)
    assert not window.search_activity.active and not window.search_activity.timer.isActive()
    assert '검색 중단' in window.search_activity.heading.text()
    previous=window.search_activity.sources[provider.name]['count']
    window.update_search_activity('obsolete',dict(source=provider.name,state='complete',added=999))
    assert window.search_activity.sources[provider.name]['count']==previous
    window.navigate('local')
    assert window.search_activity.isHidden()
    window.close()


def test_zero_results_and_rejected_site_are_not_displayed_as_searching(qtbot,searches):
    from app.main_window import MainWindow
    search,provider=searches
    provider.search_images=lambda *args:([],None)
    class Blocked:
        name='blocked-fixture'
        def search_images(self,*args):raise RemoteError('HTTP 403',blocked=True)
    window=MainWindow(search.db,search.online.settings);qtbot.addWidget(window);window.online_runner.timer.stop()
    window.internet_search.providers={provider.name:provider,Blocked.name:Blocked()}
    window.search.setText('이서');window.start_internet_search()
    qtbot.waitUntil(lambda:window.search_flight is None)
    assert '403' in window.search_activity.sources[Blocked.name]['label'].text()
    assert '0' in window.search_activity.sources[provider.name]['label'].text()
    assert not window.search_activity.active and not window.search_activity.timer.isActive()
    assert '실패' in window.search_activity.heading.text()
    window.close()
