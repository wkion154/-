"""Owned synthetic files; no live source/original quality claims."""
import json
import threading
from urllib.parse import quote
from pathlib import Path
import pytest
from app.analysis_filters import AnalysisFilter
from app.library import LibraryRepository,LibraryService
from app.online_service import OnlineService
from app.page_discovery import ImageCandidate,ParsedPage
from app.provided_files import resolve
from app.providers.http import RemoteError,Cancelled
from app.providers.kpopping import provided_file_url
from app.providers.kpopping_browser import KpoppingBrowserProvider,OBSERVED_PAGES
from app.search_service import SearchService
from test_sources import FixtureHttp,image_bytes,EVIDENCE


@pytest.fixture
def files(db,settings):
    online=OnlineService(db,settings,None);provider=KpoppingBrowserProvider(online);repo=online.web.repo
    cdn='https://cdn.kpopping.com/kpics/synthetic.jpg'
    preview='https://kpopping.com/api/kpics-image?url='+quote(cdn,safe='')+'&size=thumb'
    page=repo.add_page(provider.name,OBSERVED_PAGES['장원영'])
    repo.index_page(repo.page(page),ParsedPage('Synthetic',[ImageCandidate(preview,'fixture','Fixture','thumbnail')]))
    identity=repo.images(page_id=page)[0]['id'];resource='web:'+identity
    repo.grant_many(provider.name,dict(metadata_search='allowed',thumbnail='allowed'),EVIDENCE,enabled=True)
    repo.grant(provider.name,'thumbnail','allowed',EVIDENCE,resource)
    resources={preview:(image_bytes((100,150)),'image/png'),cdn:(image_bytes((1200,1800)),'image/png')}
    client=FixtureHttp(resources);client.allowed_hosts=repo.spec(provider.name).domains
    online.web.http_factory=lambda _:client
    return online,identity,client,resources,preview,cdn


def allow_file(online,identity):
    repo=online.web.repo
    repo.grant('kpopping-browser','original_download','allowed',EVIDENCE)
    repo.grant('kpopping-browser','original_download','allowed',EVIDENCE,'web:'+identity)


def test_exact_provided_url_has_no_synthesized_size_or_host():
    cdn='https://cdn.kpopping.com/kpics/fixture.png'
    wrapper='https://kpopping.com/api/kpics-image?url='+quote(cdn,safe='')+'&size=medium'
    assert provided_file_url(wrapper)==cdn
    assert provided_file_url(wrapper+'&url='+quote(cdn,safe='')) is None
    for candidate in ('https://wrong.example/kpics/a.jpg','https://cdn.kpopping.com/profiles/a.jpg','http://cdn.kpopping.com/kpics/a.jpg','https://cdn.kpopping.com:444/kpics/a.jpg'):
        assert provided_file_url('https://kpopping.com/api/kpics-image?url='+quote(candidate,safe='')) is None


def test_preview_permission_never_fetches_provided_file(files):
    online,identity,client,resources,preview,cdn=files
    row=LibraryRepository(online.db).entry('web:'+identity)
    view=LibraryService(online).original(row,threading.Event())
    assert view['preview_only'] and view['width']==100 and cdn not in client.calls
    with pytest.raises(RemoteError):resolve(online.web,identity,threading.Event())
    assert cdn not in client.calls


def test_measured_file_view_preserves_preview_and_reuses_integrity_checked_cache(files):
    online,identity,client,_,preview,cdn=files;cancel=threading.Event()
    library=LibraryService(online);row=library.repo.entry('web:'+identity)
    library.preview(row,cancel);old=online.web.repo.image(identity)
    allow_file(online,identity);view=library.original(row,cancel)
    assert (view['width'],view['height'])==(1200,1800) and view['provided_file_only']
    assert view['id']==row['id'] and view['sha256']!=old['sha256']
    assert online.web.repo.image(identity)['cache_path']==old['cache_path']
    assert library.original(row,cancel)['path']==view['path'] and client.calls.count(cdn)==1
    Path(view['path']).write_bytes(b'changed')
    with pytest.raises(RemoteError,match='캐시가 변경'):library.original(row,cancel)
    assert client.calls.count(cdn)==1


def test_provided_file_save_reuses_search_object_favorite_and_rights(files):
    online,identity,client,_,preview,cdn=files;cancel=threading.Event();repo=LibraryRepository(online.db)
    resource='web:'+identity
    repo.favorite(resource,True)
    search=SearchService(online,[]);session=search.start('장원영',AnalysisFilter())
    with online.db.connect() as c:c.execute('INSERT INTO search_hits VALUES(?,?,0)',(session,resource))
    allow_file(online,identity)
    result=online.web.download(identity,cancel);asset=result['asset_id']
    from app.online_dialog import job_summary
    assert result['provided_file_only']
    assert '최대 원본 여부 미확인' in job_summary(dict(message='',result=json.dumps(result)))
    rows,total=repo.query(scope=('search',session),sort='discovery')
    assert total==1 and rows[0]['id']==asset and rows[0]['favorite']
    assert (rows[0]['width'],rows[0]['height'])==(1200,1800)
    with online.db.connect() as c:
        source=c.execute('SELECT * FROM asset_sources WHERE asset_id=? AND image_url=?',(asset,cdn)).fetchone()
    assert source is not None
    saved=LibraryService(online).original(repo.entry(asset),cancel)
    assert saved['provided_file_only'] and saved['sha256']==result['sha256']
    assert online.web.download(identity,cancel)['asset_id']==asset
    assert online.db.query()[1]==1
    online.web.repo.grant('kpopping-browser','original_download','denied',EVIDENCE,resource)
    with pytest.raises(RemoteError):LibraryService(online).original(repo.entry(asset),cancel)
    assert not online.web.repo.allowed('kpopping-browser','ai_restore_input',resource)


def test_cancel_policy_failure_and_no_original_address_never_save_preview(files,monkeypatch):
    online,identity,client,_,preview,cdn=files;allow_file(online,identity)
    cancelled=threading.Event();cancelled.set()
    with pytest.raises(Cancelled):resolve(online.web,identity,cancelled)
    assert not client.calls
    def forbidden(*args):raise RemoteError('Synthetic robots denial',blocked=True)
    monkeypatch.setattr(online.web,'robots',forbidden)
    with pytest.raises(RemoteError,match='robots'):resolve(online.web,identity,threading.Event())
    assert cdn not in client.calls and online.db.query()[1]==0
    with online.db.connect() as c:c.execute('UPDATE web_images SET url=? WHERE id=?',('https://kpopping.com/small.jpg',identity))
    with pytest.raises(RemoteError,match='주소를 확인'):online.web.download(identity,threading.Event())
    assert online.db.query()[1]==0


def test_revoked_permission_during_file_download_is_rechecked(files,monkeypatch):
    online,identity,client,_,_,cdn=files;allow_file(online,identity)
    original=client.open
    def revoke(url,cancelled,accept=None):
        response=original(url,cancelled,accept)
        if url==cdn:online.web.repo.grant('kpopping-browser','original_download','denied',EVIDENCE,'web:'+identity)
        return response
    monkeypatch.setattr(client,'open',revoke)
    with pytest.raises(RemoteError):resolve(online.web,identity,threading.Event())
    assert online.db.query()[1]==0
    with online.db.connect() as c:assert c.execute('SELECT COUNT(*) FROM web_revisions').fetchone()[0]==0


def test_source_permission_alone_does_not_allow_file_and_cached_file_obeys_denial(files):
    online,identity,client,_,_,cdn=files;repo=online.web.repo;cancel=threading.Event()
    repo.grant('kpopping-browser','original_download','allowed',EVIDENCE)
    with pytest.raises(RemoteError):resolve(online.web,identity,cancel)
    assert cdn not in client.calls
    allow_file(online,identity);resolve(online.web,identity,cancel)
    repo.grant('kpopping-browser','original_download','denied',EVIDENCE,'web:'+identity)
    with pytest.raises(RemoteError):resolve(online.web,identity,cancel)
    assert client.calls.count(cdn)==1
