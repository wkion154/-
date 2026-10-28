"""Synthetic feed responses only; these tests are not live Flickr evidence."""
import json
import threading
import pytest
from app.online_service import OnlineService
from app.providers.flickr_feed import FlickrFeedProvider, parse_feed
from app.providers.http import RemoteError
from app.library import LibraryRepository, LibraryService
from app.search_service import SearchService
from app.analysis_filters import AnalysisFilter
from test_online import FakeHttp
from test_sources import image_bytes


def feed_item(number='123', **changes):
    return dict(link=f'https://www.flickr.com/photos/fixture/{number}/',
                media={'m': f'https://live.staticflickr.com/1/{number}_fixture_m.jpg'},
                title='Synthetic photo', author='Synthetic author', **changes)


def test_feed_rejects_unregistered_urls_bad_shapes_and_duplicate_posts():
    valid=feed_item()
    evil=feed_item('124');evil['media']['m']='https://unregistered.example/a.jpg'
    wrong_page=feed_item('125');wrong_page['link']='https://www.flickr.com/explore'
    assert len(parse_feed(json.dumps({'items':[valid,valid,evil,wrong_page,None]})))==1
    assert len(parse_feed(json.dumps({'items':[feed_item(str(i)) for i in range(40)]})))==20
    for raw in ('<html>Access denied</html>','[]','{"items":null}'):
        with pytest.raises(RemoteError):parse_feed(raw)


def test_feed_existing_gallery_dedup_preview_and_item_denial(db,settings,monkeypatch):
    online=OnlineService(db,settings,None);provider=FlickrFeedProvider(online)
    evidence={'reference':'fixture://policy','note':'Synthetic test only; not live review.'}
    online.web.repo.grant_many(provider.name,dict(metadata_search='allowed',thumbnail='allowed'),evidence,enabled=True)
    requests=[]
    def read(source,url,*args):
        requests.append(url)
        return json.dumps({'items':[feed_item()]}).encode(),{}
    monkeypatch.setattr(online.web,'read',read)
    cancel=threading.Event();entries,cursor=provider.search_images('장원영',None,cancel)
    assert cursor is None and len(entries)==1 and 'nojsoncallback=1' in requests[0]
    assert provider.search_images('Jang Wonyoung',None,cancel)[0]==entries
    repo=online.web.repo
    page=repo.image(entries[0][4:])['page_id']
    with db.connect() as c:c.execute('UPDATE source_pages SET next_due=0 WHERE id=?',(page,))
    class Queue:
        def enqueue(self,*args,**kwargs):pytest.fail('Feed photos must not be refreshed by the generic HTML scanner')
    online.web.schedule(Queue())
    with pytest.raises(RemoteError,match='검색 전용'):online.web.scan(page,cancel)
    repo.grant(provider.name,'thumbnail','denied',evidence,entries[0])
    provider.search_images('장원영',None,cancel)
    assert not repo.allowed(provider.name,'thumbnail',entries[0])
    repo.grant(provider.name,'thumbnail','allowed',evidence,entries[0])
    def client(spec):
        http=FakeHttp(image_bytes());http.allowed_hosts=spec.domains;return http
    online.web.http_factory=client;monkeypatch.setattr(online.web,'robots',lambda *args:None)
    row=LibraryRepository(db).entry(entries[0]);view=LibraryService(online).original(row,cancel)
    assert view['preview_only'] and view['width']==800
    for operation in ('original_download','local_analysis','ai_restore_input'):
        assert not repo.allowed(provider.name,operation,entries[0])


def test_feed_never_requests_before_permission_or_after_cancellation(db,settings,monkeypatch):
    online=OnlineService(db,settings,None);provider=FlickrFeedProvider(online)
    monkeypatch.setattr(online.web,'read',lambda *args:pytest.fail('Unexpected network request'))
    with pytest.raises(RemoteError):provider.search_images('아이유',None,threading.Event())
    with pytest.raises(RemoteError):provider.search_images('아이유',{'page':2},threading.Event())


def test_search_registry_restores_flickr_without_wikimedia(db,settings):
    settings.values['flickr_feed_enabled']=True
    search=SearchService(OnlineService(db,settings,None))
    assert list(search.providers)==['flickr-public-feed']
