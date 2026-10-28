import threading
import pytest
from app.online_service import OnlineService
from app.providers.kpopping_browser import KpoppingBrowserProvider,OBSERVED_PAGES
from app.providers.http import RemoteError
from app.library import LibraryService,LibraryRepository
from test_sources import image_bytes
from test_online import FakeHttp


def test_browser_adapter_uses_existing_gallery_and_preview_only_viewer(db,settings,monkeypatch):
    import app.providers.kpopping_browser as module
    online=OnlineService(db,settings,None);provider=KpoppingBrowserProvider(online)
    evidence={'reference':'fixture://owned-test','note':'Synthetic provider response, not live policy evidence.'}
    online.web.repo.grant_many(provider.name,dict(metadata_search='allowed',thumbnail='allowed'),evidence,enabled=True)
    monkeypatch.setattr(module.platform,'system',lambda:'Windows')
    url='https://kpopping.com/api/kpics-image?url=https%3A%2F%2Fcdn.kpopping.com%2Fkpics%2Ffixture.jpg&size=thumb'
    monkeypatch.setattr(provider,'_worker',lambda request,cancel:dict(page_url=OBSERVED_PAGES['장원영'],title='FIXTURE',items=[dict(url=url,logical_key='photo',title='Fixture photo',role='observed_preview',provenance=[])],next_batch=1))
    entries,cursor=provider.search_images('장원영',None,threading.Event());assert len(entries)==1 and cursor==dict(batch=1)
    def fixture_client(spec):
        client=FakeHttp(image_bytes());client.allowed_hosts=spec.domains;return client
    online.web.http_factory=fixture_client;monkeypatch.setattr(online.web,'robots',lambda *args:None)
    row=LibraryRepository(db).entry(entries[0]);library=LibraryService(online)
    image=library.preview(row,threading.Event());assert not image.isNull()
    view=library.original(row,threading.Event());assert view['preview_only'] and view['width']>0
    assert not online.web.repo.allowed(provider.name,'original_download',entries[0])
    assert not online.web.repo.allowed(provider.name,'ai_restore_input',entries[0])


def test_browser_adapter_refuses_unreviewed_source_and_preserves_item_denial(db,settings,monkeypatch):
    import app.providers.kpopping_browser as module
    provider=KpoppingBrowserProvider(OnlineService(db,settings,None));monkeypatch.setattr(module.platform,'system',lambda:'Windows')
    monkeypatch.setattr(provider,'_worker',lambda *_:pytest.fail('Browser must not start before permission'))
    with pytest.raises(RemoteError):provider.search_images('장원영',None,threading.Event())
