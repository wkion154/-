import pytest
from app.browser_search_worker import launch_browser


class Chromium:
    def __init__(self,fail=()):self.calls=[];self.fail=fail;self.browser=object()
    def launch(self,**options):
        self.calls.append(options)
        if options['channel'] in self.fail:raise RuntimeError('Fixture browser unavailable')
        return self.browser


def test_default_search_has_no_visible_window_and_keeps_sandbox():
    chromium=Chromium()
    assert launch_browser(chromium) is chromium.browser
    assert chromium.calls==[dict(channel='msedge',headless=True,chromium_sandbox=True)]


def test_explicit_diagnostic_mode_shows_browser():
    chromium=Chromium()
    launch_browser(chromium,show_browser=True)
    assert chromium.calls[0]['headless'] is False


def test_chrome_fallback_keeps_background_mode():
    chromium=Chromium(fail=('msedge',))
    assert launch_browser(chromium) is chromium.browser
    assert [call['channel'] for call in chromium.calls]==['msedge','chrome']
    assert all(call['headless'] and call['chromium_sandbox'] for call in chromium.calls)


def test_failure_does_not_open_visible_window_as_automatic_fallback():
    chromium=Chromium(fail=('msedge','chrome'))
    with pytest.raises(RuntimeError,match='검색 브라우저 표시'):
        launch_browser(chromium)
    assert len(chromium.calls)==2 and all(call['headless'] for call in chromium.calls)


@pytest.mark.parametrize('visible',[False,True])
def test_saved_visibility_option_reaches_browser_worker(db,settings,monkeypatch,visible):
    import threading
    import app.providers.kpopping_browser as module
    from app.online_service import OnlineService
    from app.providers.kpopping_browser import KpoppingBrowserProvider,OBSERVED_PAGES
    online=OnlineService(db,settings,None);provider=KpoppingBrowserProvider(online)
    online.web.repo.grant_many(provider.name,dict(metadata_search='allowed'),
                              dict(reference='fixture://visibility',note='Synthetic only'),enabled=True)
    settings.values['kpopping_show_browser']=visible
    monkeypatch.setattr(module.platform,'system',lambda:'Windows')
    requests=[]
    def worker(request,cancel):
        requests.append(request)
        return dict(page_url=OBSERVED_PAGES['장원영'],title='Fixture',items=[],next_batch=None,filter_verified=True)
    monkeypatch.setattr(provider,'_worker',worker)
    provider.search_images('장원영',None,threading.Event())
    assert requests[0]['show_browser'] is visible
