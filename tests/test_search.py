import threading
import pytest
from app.analysis_filters import AnalysisFilter
from app.search_service import SearchService
from app.online_service import OnlineService
from app.library import LibraryRepository
from app.page_discovery import discover_html
from app.providers.http import RemoteError,Cancelled
from app.providers.page_search import PageSearchProvider

@pytest.fixture
def searches(db,settings):
    online=OnlineService(db,settings,None);repo=online.web.repo
    repo.save_spec(dict(id='owned-test',name='FICTIONAL TEST SOURCE',category='user',adapter='html',domains=['owned.example']))
    repo.enable('owned-test',True)
    page=repo.add_page('owned-test','https://owned.example/fixture')
    repo.index_page(repo.page(page),discover_html('<img src="https://owned.example/test.png">','https://owned.example/fixture'))
    entry='web:'+repo.images(page_id=page)[0]['id']
    class Provider:
        name='owned-test'
        def __init__(self):self.calls=[]
        def search_images(self,query,cursor,cancelled):
            self.calls.append((query,cursor));return [entry],{'offset':30} if not cursor else None
    provider=Provider();return SearchService(online,[provider]),provider


def test_sessions_pagination_dedup_use_existing_web_gallery(searches):
    search,provider=searches;session=search.start('장원영',AnalysisFilter())
    assert search.page(session,threading.Event())['added']==1
    rows,total=LibraryRepository(search.db).query(scope=('search',session));assert total==1 and rows[0]['id'].startswith('web:')
    assert search.page(session,threading.Event())==dict(added=0,state='exhausted')
    search.page(session,threading.Event());assert len(provider.calls)==2
    with search.db.connect() as c:assert c.execute('SELECT COUNT(*) FROM image_person').fetchone()[0]==0


def test_alias_composition_expansion_and_session_isolation(searches):
    search,_=searches;search.db.add_entity('person','장원영',aliases='Jang Wonyoung,IVE Wonyoung')
    assert len(search.variants('장원영',AnalysisFilter(composition='full_body')))==3
    first=search.start('장원영',AnalysisFilter());search.page(first,threading.Event());second=search.start('aespa',AnalysisFilter())
    assert LibraryRepository(search.db).query(scope=('search',second))[1]==0


def test_no_verified_source_blocks_instead_of_falling_back_to_commons(searches):
    search,_=searches;search.providers={};session=search.start('아이유',AnalysisFilter())
    with pytest.raises(RemoteError,match='제외'):search.page(session,threading.Event())
    assert search.session(session)['state']=='blocked'


def test_cancel_and_same_cursor_prevent_extra_pages(searches):
    search,provider=searches;session=search.start('IVE',AnalysisFilter());cancel=threading.Event();cancel.set()
    with pytest.raises(Cancelled):search.page(session,cancel)
    assert not provider.calls
    search.page(session,threading.Event());provider.search_images=lambda q,c,e:([],c)
    with pytest.raises(RemoteError,match='커서'):search.page(session,threading.Event())


def test_html_template_is_origin_checked_and_requires_review(searches):
    search,_=searches;web=search.online.web
    with pytest.raises(ValueError):PageSearchProvider(web,'owned-test','https://owned.example/no-placeholder')
    with pytest.raises(RemoteError):PageSearchProvider(web,'owned-test','https://other.example/?q={query}')
    adapter=PageSearchProvider(web,'owned-test','https://owned.example/search?q={query}')
    with pytest.raises(RemoteError):adapter.search_images('장원영',None,threading.Event())
    with pytest.raises(ValueError):PageSearchProvider(web,'wikimedia','https://commons.wikimedia.org/?q={query}')


def test_ui_uses_existing_gallery_and_cancels_on_navigation(qtbot,searches):
    from app.main_window import MainWindow
    search,provider=searches;window=MainWindow(search.db,search.online.settings);qtbot.addWidget(window);window.online_runner.timer.stop()
    window.internet_search.providers={provider.name:provider};window.show();window.search.setText('장원영');window.start_internet_search()
    qtbot.waitUntil(lambda:window.search_flight is None and window.model.total==1)
    assert window.scope[0]=='search' and window.model.rows[0]['kind']=='web'
    window.navigate('local');qtbot.waitUntil(lambda:window.model.total==0);assert window.search_session is None;window.close()


def test_one_failed_provider_does_not_block_the_next(searches):
    search,provider=searches
    class Failed:
        name='failed-fixture'
        def search_images(self,*args):raise RemoteError('test failure')
    search.providers={'failed-fixture':Failed(),provider.name:provider}
    session=search.start('aespa',AnalysisFilter());result=search.page(session,threading.Event())
    assert 'test failure' in result['error'] and result['state']=='ready' and result['added']==1
    assert search.page(session,threading.Event())['added']==0


def test_fast_source_is_published_before_slow_source_finishes(searches):
    import time
    search,provider=searches;first_arrived=threading.Event();slow_entered=threading.Event();observed=[]
    class Slow:
        name='slow-fixture'
        def search_images(self,*args):
            slow_entered.set()
            assert first_arrived.wait(3),'Fast source should publish before this source is released'
            return [],None
    search.providers={provider.name:provider,'slow-fixture':Slow()}
    session=search.start('장원영',AnalysisFilter())
    def partial(identity):
        observed.append(LibraryRepository(search.db).query(scope=('search',identity))[1]);first_arrived.set()
    result=search.page(session,threading.Event(),partial)
    assert result['added']==1 and observed[0]==1 and slow_entered.is_set()


def test_blocked_provider_is_not_retried_for_every_alias(searches):
    search,provider=searches;calls=[]
    class Blocked:
        name='blocked-fixture'
        def search_images(self,*args):calls.append(1);raise RemoteError('denied',blocked=True)
    search.providers={provider.name:provider,'blocked-fixture':Blocked()}
    search.db.add_entity('person','장원영',aliases='Jang Wonyoung,IVE Wonyoung')
    session=search.start('장원영',AnalysisFilter())
    for _ in range(8):
        result=search.page(session,threading.Event())
        if result['state']=='exhausted':break
    assert len(calls)==1


def test_discovery_order_appends_and_locates_across_session_and_saved_asset(searches,photo):
    from app.images import inspect_image
    search,provider=searches;repo=search.online.web.repo
    session=search.start('장원영',AnalysisFilter());search.page(session,threading.Event())
    first=LibraryRepository(search.db).query(scope=('search',session),sort='discovery')[0][0]['id']
    page=repo.add_page('owned-test','https://owned.example/second')
    repo.index_page(repo.page(page),discover_html('<img src="https://owned.example/second.png">','https://owned.example/second'))
    second='web:'+repo.images(page_id=page)[0]['id']
    provider.search_images=lambda *args:([second],None)
    search.page(session,threading.Event())
    library=LibraryRepository(search.db)
    rows,total=library.query(scope=('search',session),sort='discovery')
    assert total==2 and [r['id'] for r in rows]==[first,second]
    assert library.locate(second,scope=('search',session),sort='discovery')==1
    # A different session's discovery positions must not affect this one.
    other=search.start('aespa',AnalysisFilter());search.page(other,threading.Event())
    meta=inspect_image(photo)
    asset,_=search.db.register(meta,origin='local')
    repo.measured(first[4:],meta,photo)
    repo.attach(repo.image(first[4:]),asset)
    rows,total=library.query(scope=('search',session),sort='discovery')
    assert total==2 and [r['id'] for r in rows]==[asset,second]
    assert library.locate(first,scope=('search',session),sort='discovery')==0


def test_scroll_requests_once_and_pauses_on_duplicate_only_page(qtbot,searches):
    from app.main_window import MainWindow
    search,provider=searches;window=MainWindow(search.db,search.online.settings);qtbot.addWidget(window);window.online_runner.timer.stop()
    window.internet_search.providers={provider.name:provider};window.show();window.search.setText('장원영');window.start_internet_search()
    qtbot.waitUntil(lambda:window.search_flight is None and window.model.total==1 and not window.model.loading)
    assert window.search_auto_ready
    # A result reload alone must not issue another browser/network request.
    qtbot.wait(180);assert len(provider.calls)==1
    window.gallery.more_requested.emit();window.gallery.more_requested.emit()
    qtbot.waitUntil(lambda:len(provider.calls)==2 and window.search_flight is None)
    window.gallery.more_requested.emit();qtbot.wait(180)
    assert len(provider.calls)==2 and not window.search_auto_ready
    assert window.model.total==1
    window.navigate('local');window.gallery.more_requested.emit();qtbot.wait(180)
    assert len(provider.calls)==2;window.close()


def test_search_materialized_members_work_with_color_cte_and_explicit_sort(searches):
    search,provider=searches;session=search.start('장원영',AnalysisFilter())
    search.page(session,threading.Event())
    library=LibraryRepository(search.db,filters=AnalysisFilter(color='#ff88aa',include_unknown=True))
    for sort in ('discovery','newest','name','color_share'):
        rows,total=library.query(scope=('search',session),sort=sort)
        assert total==1 and library.locate(rows[0]['id'],scope=('search',session),sort=sort)==0


def test_single_blocked_provider_stops_before_trying_aliases(searches):
    search,provider=searches
    search.db.add_entity('person','장원영',aliases='Jang Wonyoung,IVE Wonyoung')
    def denied(*args):
        provider.calls.append(args)
        raise RemoteError('Access denied',blocked=True)
    provider.search_images=denied
    session=search.start('장원영',AnalysisFilter())
    with pytest.raises(RemoteError,match='Access denied'):
        search.page(session,threading.Event())
    assert search.session(session)['state']=='blocked'
    assert search.session(session)['variant']==0 and len(provider.calls)==1
    again=search.page(session,threading.Event())
    assert again['state']=='blocked' and again['added']==0 and len(provider.calls)==1


@pytest.mark.parametrize('cursor',[0,{},[],False])
def test_falsey_page_cursor_is_preserved(searches,cursor):
    search,provider=searches;calls=[]
    def pages(query,previous,cancelled):
        calls.append(previous)
        return [],cursor if len(calls)==1 else None
    provider.search_images=pages
    session=search.start('IVE',AnalysisFilter())
    assert search.page(session,threading.Event())['state']=='ready'
    assert search.page(session,threading.Event())['state']=='exhausted'
    assert calls==[None,cursor]


@pytest.mark.parametrize('parallel',[False,True])
def test_entity_picker_gets_exact_alias_without_discovery_hints(searches,parallel):
    search,provider=searches;provider.identity_search=True
    search.db.add_entity('person','장원영',aliases='Jang Wonyoung')
    free_queries=[]
    class FreeText:
        name='free-text-fixture'
        def search_images(self,query,cursor,cancelled):
            free_queries.append(query);return [],None
    if parallel:search.providers[FreeText.name]=FreeText()
    filters=AnalysisFilter(composition='full_body',color='#ff88aa')
    session=search.start('장원영',filters)
    for _ in range(8):
        if search.page(session,threading.Event())['state']=='exhausted':break
    assert set(query for query,_ in provider.calls)=={'장원영','Jang Wonyoung'}
    if parallel:assert free_queries==['장원영 full body full length #ff88aa','Jang Wonyoung full body full length #ff88aa']


def test_blocked_search_disables_more_button_and_new_search_can_retry(qtbot,searches):
    from app.main_window import MainWindow
    search,provider=searches;calls=[]
    def denied(*args):
        calls.append(1);raise RemoteError('Fixture blocked',blocked=True)
    provider.search_images=denied
    window=MainWindow(search.db,search.online.settings);qtbot.addWidget(window)
    window.online_runner.timer.stop();window.internet_search.providers={provider.name:provider}
    window.search.setText('장원영');window.start_internet_search()
    qtbot.waitUntil(lambda:window.search_flight is None)
    assert not window.next_search.isEnabled() and not window.search_auto_ready
    window.start_internet_search()
    qtbot.waitUntil(lambda:window.search_flight is None)
    assert len(calls)==2
    window.close()
