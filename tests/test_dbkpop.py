import json,threading
import pytest
from app.providers.dbkpop import DBKpopProvider,parse_posts,ENDPOINT
from app.providers.http import RemoteError
from app.library import LibraryRepository,LibraryService
from app.provided_files import source_url
from test_sources import FixtureHttp,image_bytes,EVIDENCE


def post():
    return dict(link='https://dbkpop.com/2026/example/',title=dict(rendered='IVE &amp; Leeseo'),
        content=dict(rendered='<a href="https://dbkpop.com/wp-content/uploads/full.jpg"><img src="https://dbkpop.com/wp-content/uploads/thumb.jpg" width="300" height="400" alt="Fixture"></a><img src="https://dbkpop.com/logo.png" width="30" height="30"><img src="https://evil.example/a.jpg" width="300" height="400">'))


def test_parser_uses_only_small_supplied_uploads_and_exact_file_link():
    page,parsed=parse_posts(json.dumps([post()]))[0]
    assert parsed.title=='IVE & Leeseo' and len(parsed.candidates)==1
    candidate=parsed.candidates[0]
    assert candidate.role=='thumbnail' and candidate.declared_width==300
    assert candidate.provenance[0]['provided_resource_url']=='https://dbkpop.com/wp-content/uploads/full.jpg'


def test_search_gallery_preview_pagination_and_file_evidence(db,settings,monkeypatch):
    from app.online_service import OnlineService
    online=OnlineService(db,settings,None);provider=DBKpopProvider(online);repo=online.web.repo
    repo.grant_many(provider.name,dict(metadata_search='allowed',thumbnail='allowed'),EVIDENCE,enabled=True)
    calls=[]
    def read(source,url,*args):
        calls.append(url);return json.dumps([post()]).encode(),{'X-WP-TotalPages':'2'}
    monkeypatch.setattr(online.web,'read',read)
    entries,cursor=provider.search_images('Leeseo',None,threading.Event())
    assert cursor==dict(page=2,query='Leeseo') and len(entries)==1
    assert calls[0].startswith(ENDPOINT)
    row=LibraryRepository(db).entry(entries[0]);item=repo.image(row['ref_id'])
    assert source_url(item)=='https://dbkpop.com/wp-content/uploads/full.jpg'
    assert not repo.allowed(provider.name,'original_download',entries[0])
    client=FixtureHttp({item['url']:(image_bytes((300,400)),'image/png')});client.allowed_hosts=['dbkpop.com']
    online.web.http_factory=lambda _:client
    assert not LibraryService(online).preview(row,threading.Event()).isNull()
    again,next_cursor=provider.search_images('Leeseo',cursor,threading.Event())
    assert again==entries and next_cursor is None
    with pytest.raises(RemoteError):provider.search_images('IVE',cursor,threading.Event())


def test_no_review_or_cancel_never_requests_search(db,settings,monkeypatch):
    from app.online_service import OnlineService
    online=OnlineService(db,settings,None);provider=DBKpopProvider(online)
    monkeypatch.setattr(online.web,'read',lambda *args:pytest.fail('Must not request metadata'))
    with pytest.raises(RemoteError):provider.search_images('IVE',None,threading.Event())


@pytest.mark.parametrize('raw',['{}','not JSON',json.dumps([post()]*6)])
def test_malformed_or_oversized_batches_fail(raw):
    with pytest.raises(RemoteError):parse_posts(raw)
