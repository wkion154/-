import threading
import pytest
from app.providers.http import RemoteError
from app.providers.search_previews import allow_reviewed_previews
from test_provided_files import files, allow_file
from test_sources import EVIDENCE


def test_explicit_file_preview_uses_only_supplied_cdn_and_keeps_preview_record(files):
    online,identity,client,_,wrapper,cdn=files
    allow_file(online,identity)
    online.settings.values['kpopping_provided_preview']=True
    image=online.web.preview(identity,threading.Event())
    assert not image.isNull() and image.width()<=512 and image.height()<=512
    assert cdn in client.calls and wrapper not in client.calls
    assert online.web.repo.image(identity)['cache_path'] is None
    assert online.db.query()[1]==0
    assert not online.web.repo.allowed('kpopping-browser','ai_restore_input','web:'+identity)


def test_file_preview_opt_in_does_not_replace_source_and_photo_permissions(files):
    online,identity,client,_,_,cdn=files
    online.settings.values['kpopping_provided_preview']=True
    with pytest.raises(RemoteError):online.web.preview(identity,threading.Event())
    assert cdn not in client.calls
    online.web.repo.grant('kpopping-browser','original_download','allowed',EVIDENCE)
    with pytest.raises(RemoteError):online.web.preview(identity,threading.Event())
    assert cdn not in client.calls


def test_blocked_wrapper_without_opt_in_explains_next_step_without_fetching_cdn(files,monkeypatch):
    online,identity,client,_,wrapper,cdn=files
    allow_file(online,identity)
    def robots(*args):raise RemoteError('Blocked fixture wrapper',blocked=True)
    monkeypatch.setattr(online.web,'robots',robots)
    with pytest.raises(RemoteError,match='검색 출처'):
        online.web.preview(identity,threading.Event())
    assert cdn not in client.calls and wrapper not in client.calls


def test_cdn_policy_and_revoked_file_rights_remain_enforced(files,monkeypatch):
    online,identity,client,_,_,cdn=files
    allow_file(online,identity);online.settings.values['kpopping_provided_preview']=True
    robots=online.web.robots
    def denied(source,url,cancel):
        if url==cdn:raise RemoteError('CDN policy denied',blocked=True)
        return robots(source,url,cancel)
    monkeypatch.setattr(online.web,'robots',denied)
    with pytest.raises(RemoteError,match='CDN policy'):online.web.preview(identity,threading.Event())
    assert cdn not in client.calls
    monkeypatch.setattr(online.web,'robots',robots)
    online.web.preview(identity,threading.Event())
    online.web.repo.grant('kpopping-browser','original_download','denied',EVIDENCE,'web:'+identity)
    with pytest.raises(RemoteError):online.web.preview(identity,threading.Event())


def test_bulk_file_grant_preserves_explicit_photo_denial(files):
    online,identity,_,_,_,_=files;repo=online.web.repo
    repo.grant('kpopping-browser','original_download','allowed',EVIDENCE)
    rows=[repo.image(identity)]
    repo.grant('kpopping-browser','original_download','denied',EVIDENCE,'web:'+identity)
    allow_reviewed_previews(repo,'kpopping-browser',rows,operation='original_download')
    assert not repo.allowed('kpopping-browser','original_download','web:'+identity)


def test_search_connection_exposes_explicit_download_opt_in(qtbot,db,settings,monkeypatch):
    from PySide6.QtWidgets import QDialog,QCheckBox,QLineEdit
    from app.main_window import MainWindow
    window=MainWindow(db,settings);qtbot.addWidget(window);window.online_runner.timer.stop()
    def accept(dialog):
        for box in dialog.findChildren(QCheckBox):
            box.setChecked(box.text().startswith('Kpopping'))
        dialog.findChildren(QLineEdit)[0].setText('fixture://explicit-file-preview-review')
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,'exec',accept)
    window.configure_browser_search()
    assert settings.values['kpopping_provided_preview'] is True
    assert window.online_service.web.repo.allowed('kpopping-browser','original_download')
    assert not window.online_service.web.repo.allowed('kpopping-browser','ai_restore_input')
    window.close()


def test_search_opt_in_grants_new_candidates_but_preserves_photo_denial(files,monkeypatch):
    from app.providers.kpopping_browser import KpoppingBrowserProvider,OBSERVED_PAGES
    import app.providers.kpopping_browser as module
    online,identity,_,_,wrapper,_=files;repo=online.web.repo
    provider=KpoppingBrowserProvider(online)
    repo.grant(provider.name,'original_download','allowed',EVIDENCE)
    online.settings.values['kpopping_provided_preview']=True
    monkeypatch.setattr(module.platform,'system',lambda:'Windows')
    monkeypatch.setattr(provider,'_worker',lambda *_:dict(
        page_url=OBSERVED_PAGES['장원영'],title='Fixture',filter_verified=True,
        items=[dict(url=wrapper,logical_key='fixture',title='Fixture',provenance=[])],next_batch=None))
    provider.search_images('장원영',None,threading.Event())
    assert repo.allowed(provider.name,'original_download','web:'+identity)
    repo.grant(provider.name,'original_download','denied',EVIDENCE,'web:'+identity)
    provider.search_images('장원영',None,threading.Event())
    assert not repo.allowed(provider.name,'original_download','web:'+identity)
