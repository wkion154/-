import json,threading
import pytest
from app.providers.naver_images import NaverImageProvider,parse_results,linked_file
from app.providers.http import RemoteError,HttpClient,SafeRedirect
from app.online_service import OnlineService
from app.library import LibraryRepository,LibraryService
from app import naver_credentials
from test_sources import FixtureHttp,Response,image_bytes,EVIDENCE


def response():
    return dict(total=200,start=1,display=50,items=[dict(title='<b>Leeseo</b> photo',
        link='https://photos.example/original.jpg',thumbnail='https://search.pstatic.net/common/fixture.jpg')])


def test_api_results_keep_original_links_as_metadata_without_fetching_origins():
    data,candidates=parse_results(json.dumps(response()))
    assert len(candidates)==1 and candidates[0].title=='Leeseo photo'
    assert candidates[0].url=='https://search.pstatic.net/common/fixture.jpg'
    assert candidates[0].provenance[0]['linked_image_url']=='https://photos.example/original.jpg'
    assert linked_file(dict(provenance=json.dumps(candidates[0].provenance)))=='https://photos.example/original.jpg'
    data=response();data['items'][0]['thumbnail']='https://evil.example/stolen.jpg'
    assert parse_results(json.dumps(data))[1]==[]


def test_authenticated_api_pagination_and_existing_preview(db,settings,monkeypatch):
    import app.providers.naver_images as module
    online=OnlineService(db,settings,None);provider=NaverImageProvider(online);repo=online.web.repo
    repo.grant_many(provider.name,dict(metadata_search='allowed',thumbnail='allowed'),EVIDENCE,enabled=True)
    monkeypatch.setattr(naver_credentials,'load',lambda _:('fixture-id','fixture-secret'))
    calls=[]
    class Api:
        def open(self,url,cancelled):calls.append(url);return Response(json.dumps(response()).encode(),'application/json')
    def client(**options):
        assert options['allowed_hosts']==['openapi.naver.com']
        assert options['headers']['X-Naver-Client-Secret']=='fixture-secret'
        return Api()
    monkeypatch.setattr(module,'HttpClient',client)
    entries,cursor=provider.search_images('이서',None,threading.Event())
    assert len(entries)==1 and cursor==dict(start=51,query='이서')
    assert 'fixture-secret' not in calls[0] and 'fixture-id' not in calls[0]
    item=repo.image(entries[0][4:])
    assert not repo.allowed(provider.name,'original_download',entries[0])
    binary=FixtureHttp({item['url']:(image_bytes((200,300)),'image/png')});binary.allowed_hosts=['search.pstatic.net']
    online.web.http_factory=lambda _:binary
    row=LibraryRepository(db).entry(entries[0])
    assert not LibraryService(online).preview(row,threading.Event()).isNull()
    assert all('photos.example' not in url for url in binary.calls)


def test_missing_api_credentials_stops_only_that_source(db,settings,monkeypatch):
    import app.providers.naver_images as module
    online=OnlineService(db,settings,None);provider=NaverImageProvider(online)
    online.web.repo.grant_many(provider.name,dict(metadata_search='allowed'),EVIDENCE,enabled=True)
    def missing(_):raise RuntimeError('Enter credentials')
    monkeypatch.setattr(naver_credentials,'load',missing)
    monkeypatch.setattr(module,'HttpClient',lambda **kwargs:pytest.fail('No network without credentials'))
    with pytest.raises(RemoteError) as caught:provider.search_images('이서',None,threading.Event())
    assert caught.value.blocked


def test_api_headers_cannot_redirect_to_image_origin():
    from urllib.request import Request
    handler=SafeRedirect(['openapi.naver.com'])
    request=Request('https://openapi.naver.com/v1/search/image.json',headers={'X-Naver-Client-Secret':'fixture-secret'})
    with pytest.raises(RemoteError):handler.redirect_request(request,None,302,'redirect',{},'https://photos.example/a.jpg')


def test_credentials_use_encrypted_store_and_not_settings_json(tmp_path,monkeypatch):
    def crypt(data,decrypt=False):return data[4:][::-1] if decrypt else b'TEST'+data[::-1]
    monkeypatch.setattr(naver_credentials,'_crypt',crypt)
    monkeypatch.delenv('NAVER_CLIENT_ID',raising=False);monkeypatch.delenv('NAVER_CLIENT_SECRET',raising=False)
    naver_credentials.save(tmp_path,'fixture-id','fixture-secret')
    blob=(tmp_path/'credentials'/'naver.dpapi').read_bytes()
    assert b'fixture-secret' not in blob and b'fixture-id' not in blob
    assert naver_credentials.load(tmp_path)==('fixture-id','fixture-secret')
    assert not (tmp_path/'settings.json').exists()


def test_invalid_values_never_reach_secure_store(tmp_path,monkeypatch):
    monkeypatch.setattr(naver_credentials,'_crypt',lambda *args,**kwargs:pytest.fail('Invalid values'))
    with pytest.raises(ValueError):naver_credentials.save(tmp_path,'fixture','invalid\nheader')


def test_credentials_dialog_sends_values_only_to_secure_store(qtbot,db,settings,monkeypatch):
    from PySide6.QtWidgets import QDialog,QLineEdit
    from app.main_window import MainWindow
    window=MainWindow(db,settings);qtbot.addWidget(window);window.online_runner.timer.stop()
    captured=[]
    monkeypatch.setattr(naver_credentials,'save',lambda root,client_id,secret:captured.append((client_id,secret)))
    def accept(dialog):
        fields=dialog.findChildren(QLineEdit);fields[0].setText('fixture-id');fields[1].setText('fixture-secret')
        assert fields[1].echoMode()==QLineEdit.EchoMode.Password
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,'exec',accept)
    window.configure_naver_credentials()
    assert captured==[('fixture-id','fixture-secret')]
    assert 'fixture-secret' not in json.dumps(settings.values)
    window.close()


@pytest.mark.skipif(__import__('platform').system()!='Windows',reason='Real Windows DPAPI requires Windows')
def test_native_windows_credential_round_trip(tmp_path,monkeypatch):
    monkeypatch.delenv('NAVER_CLIENT_ID',raising=False);monkeypatch.delenv('NAVER_CLIENT_SECRET',raising=False)
    naver_credentials.save(tmp_path,'fixture-id','fixture-secret')
    assert b'fixture-secret' not in (tmp_path/'credentials'/'naver.dpapi').read_bytes()
    assert naver_credentials.load(tmp_path)==('fixture-id','fixture-secret')
