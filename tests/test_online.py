import hashlib
import io
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from PIL import Image
from app.database import Database,SCHEMA
from app.online_repository import OnlineRepository
from app.online_service import OnlineService,download_file
from app.providers.http import RemoteError,Cancelled,validate_url
from app.providers.wikimedia import WikimediaProvider,reuse_status,plain
from app.artist_rules import automatic_profile

class Response(io.BytesIO):
    def __init__(self,data):super().__init__(data);self.headers={'Content-Length':str(len(data))}

class FakeHttp:
    def __init__(self,data=b''):self.data=data;self.calls=[];self.json_response={}
    def open(self,url,cancelled,accept=None):self.calls.append(url);return Response(self.data)
    def json(self,base,params,cancelled):self.calls.append((base,params));return self.json_response

class FakeProvider:
    def __init__(self,data=b''):self.http=FakeHttp(data)
    def discover(self,cursor,cancelled):return [],None
    def entities(self,ids,cancelled):return [dict(external_id=x,name='Verified label',category='Example',aliases=[],members=[]) for x in ids]
    def photos(self,category,cursor,cancelled):return [],None

@pytest.fixture
def png():
    output=io.BytesIO();Image.new('RGB',(64,96),'#a0b0c0').save(output,format='PNG');return output.getvalue()

@pytest.fixture
def source(png):
    return dict(source_id='1234',source_url='https://commons.wikimedia.org/wiki/File:Example.png',original_url='https://upload.wikimedia.org/wikipedia/commons/1/12/Example.png',thumbnail_url='https://upload.wikimedia.org/wikipedia/commons/thumb/1/12/Example.png/512px-Example.png',title='File:Example.png',width=64,height=96,size=len(png),mime='image/png',source_sha1=hashlib.sha1(png).hexdigest(),license_name='CC BY-SA 4.0',license_url='https://creativecommons.org/licenses/by-sa/4.0/',attribution='Fixture author',restrictions='',license_status='eligible',captured=None,published='2026-01-01T00:00:00Z')


def approved(repo):
    repo.record_review({'documents':[{'url':'fixture://policy','sha256':'test-only'}],'scope':'TEST FIXTURE, NOT LIVE POLICY REVIEW'})
    from app.source_repository import SourceRepository
    sources=SourceRepository(repo.db)
    for operation in ('metadata_search','thumbnail','original_download'):
        sources.grant('wikimedia',operation,'allowed',{'reference':'fixture://policy','note':'TEST FIXTURE, NOT LIVE REVIEW'})
    sources.enable('wikimedia',True)

def target(repo,kind='person',external_id='Q1234',name='Fixture Artist'):
    repo.candidate(dict(external_id=external_id,kind=kind,name=name,category='Fixture',aliases=[]))
    identity=repo.accept(external_id,gender='female')
    return identity,repo.targets()[-1]['id']


def test_v1_migration_backup_and_user_data_preserved(tmp_path):
    path=tmp_path/'old.db'
    conn=sqlite3.connect(path);conn.executescript(SCHEMA)
    conn.execute("INSERT INTO groups VALUES('old','User Name','mixed','User Alias')")
    conn.execute('INSERT INTO schema_migrations VALUES(1,0)');conn.execute('PRAGMA user_version=1');conn.commit();conn.close()
    db=Database(path)
    assert db.entities()['groups'][0]['name']=='User Name'
    backups=list(tmp_path.glob('*.bak'));assert len(backups)==1
    with sqlite3.connect(backups[0]) as saved:
        assert saved.execute('PRAGMA user_version').fetchone()[0]==1
        assert saved.execute('SELECT name FROM groups').fetchone()[0]=='User Name'
    Database(path)
    assert len(list(tmp_path.glob('*.bak')))==1
    with db.connect() as migrated:assert migrated.execute('PRAGMA user_version').fetchone()[0]==6


def test_external_identity_never_merges_same_name_or_overwrites_user(db):
    repo=OnlineRepository(db)
    first,_=target(repo)
    repo.candidate(dict(external_id='Q5678',kind='person',name='Fixture Artist'))
    with pytest.raises(ValueError,match='같은 이름'):repo.accept('Q5678')
    db.rename('person',first,'My Display Name','My Alias')
    repo.candidate(dict(external_id='Q1234',kind='person',name='Remote rename',category='Updated'))
    repo.accept('Q1234',local_id=first)
    assert db.entities()['persons'][0]['name']=='My Display Name'
    assert len(db.entities()['persons'])==1
    with pytest.raises(ValueError,match='다른 외부'):repo.accept('Q5678',local_id=first)


def test_remote_index_upserts_keep_many_targets_and_no_fake_local_original(db,source):
    repo=OnlineRepository(db)
    _,a=target(repo)
    _,b=target(repo,external_id='Q9999',name='Other')
    repo.index_images(a,[source],{'gcmcontinue':'next'})
    repo.index_images(b,[source],None)
    assert len(repo.images())==1 and len(repo.images(a))==len(repo.images(b))==1
    assert db.query()[1]==0
    assert repo.images()[0]['captured'] is None
    assert repo.images()[0]['local_asset_id'] is None


def test_job_queue_dedup_checkpoint_restart_and_priority(db):
    now=[1000.0];repo=OnlineRepository(db,lambda:now[0])
    discover=repo.enqueue('discover',{})
    assert repo.enqueue('discover',{})==discover
    download=repo.enqueue('download',{'remote_id':'x'})
    claimed=repo.claim();assert claimed['id']==download
    repo.recover();assert next(x for x in repo.jobs() if x['id']==download)['state']=='retry'
    repo.finish(download,'done')
    claimed=repo.claim();assert claimed['id']==discover
    repo.finish(discover,'queued',result={'page':1},payload={'cursor':'Q100'})
    claimed=repo.claim();assert claimed['payload']['cursor']=='Q100' and claimed['attempts']==1
    repo.finish(discover,'done')
    assert repo.enqueue('discover',{})==discover
    assert repo.claim() is None
    now[0]+=86401;repo.enqueue('discover',{});assert repo.claim()['id']==discover


def test_policy_gate_blocks_before_any_network(db,settings):
    provider=FakeProvider();service=OnlineService(db,settings,provider)
    identity=service.repo.enqueue('discover',{})
    result=service.run_one(threading.Event())
    assert result['state']=='blocked'
    assert provider.http.calls==[]
    assert service.repo.jobs()[0]['id']==identity


def test_retry_after_failure_does_not_block_independent_job(db,settings,monkeypatch):
    now=[1000.0];service=OnlineService(db,settings,FakeProvider(),lambda:now[0]);approved(service.repo)
    bad=service.repo.enqueue('photos',{'target_id':'bad'})
    same_source=service.repo.enqueue('discover',{})
    good=service.repo.enqueue('web_scan',{'page_id':'independent-source-fixture'})
    def execute(job,cancel):
        if job['id']==bad:raise RemoteError('429',retryable=True,retry_after=90)
        return {'ok':True},None
    monkeypatch.setattr(service,'execute',execute)
    assert service.run_one(threading.Event())['state']=='retry'
    assert service.run_one(threading.Event())['id']==good
    assert service.run_one(threading.Event()) is None
    assert service.repo.job_state(same_source)=='queued'
    now[0]+=90
    assert service.repo.claim()['id']==bad


def test_cancellation_is_resumable(db,settings,monkeypatch):
    service=OnlineService(db,settings,FakeProvider());approved(service.repo)
    service.repo.enqueue('discover',{'cursor':'Q100'})
    def cancelled(job,event):raise Cancelled()
    monkeypatch.setattr(service,'execute',cancelled)
    service.run_one(threading.Event())
    job=service.repo.claim()
    assert job['payload']=={'cursor':'Q100'}


def test_streamed_download_hash_validation_atomicity_and_cancellation(tmp_path,png):
    path=tmp_path/'original.png';http=FakeHttp(png)
    result=download_file(http,'https://upload.wikimedia.org/example.png',path,threading.Event(),expected_size=len(png),expected_sha1=hashlib.sha1(png).hexdigest())
    assert path.read_bytes()==png and result['sha256']==hashlib.sha256(png).hexdigest()
    bad=tmp_path/'bad.png'
    with pytest.raises(RemoteError,match='SHA-1'):download_file(http,'https://upload.wikimedia.org/example.png',bad,threading.Event(),expected_sha1='0'*40)
    assert not bad.exists() and not list(tmp_path.glob('*.part'))
    path.write_bytes(b'User changed this')
    with pytest.raises(RemoteError,match='덮어쓰지'):download_file(http,'https://upload.wikimedia.org/example.png',path,threading.Event())
    assert path.read_bytes()==b'User changed this'
    cancel=threading.Event();cancel.set()
    with pytest.raises(Cancelled):download_file(http,'https://upload.wikimedia.org/example.png',bad,cancel)
    assert not bad.exists()


def test_thumbnail_cache_and_explicit_original_import_identity(db,settings,source,png):
    service=OnlineService(db,settings,FakeProvider(png));repo=service.repo;approved(repo)
    person,target_id=target(repo)
    repo.index_images(target_id,[source],None);remote=repo.images()[0]
    image=service.thumbnail(remote['id'],threading.Event());assert image.width()==64
    calls=len(service.provider.http.calls)
    service.thumbnail(remote['id'],threading.Event());assert len(service.provider.http.calls)==calls
    repo.enqueue('download',dict(remote_id=remote['id'],target_id=target_id,person_confirmed=False))
    service.run_one(threading.Event())
    row=db.query()[0][0]
    assert row['sha256']==hashlib.sha256(png).hexdigest()
    assert db.associations(row['id'])[0]==[]
    assert repo.source_for_asset(row['id'])[0]['attribution']=='Fixture author'
    assert row['captured'] is None
    repo.enqueue('download',dict(remote_id=remote['id'],target_id=target_id,person_confirmed=True),force=True)
    before=len(service.provider.http.calls)
    service.run_one(threading.Event())
    assert len(service.provider.http.calls)==before
    assert db.associations(row['id'])[0][0]['id']==person
    assert db.query()[1]==1


def test_review_required_license_never_fetches_image(db,settings,source,png):
    service=OnlineService(db,settings,FakeProvider(png));approved(service.repo)
    _,target_id=target(service.repo)
    service.repo.index_images(target_id,[source|{'license_status':'review'}],None)
    remote=service.repo.images()[0]
    with pytest.raises(RemoteError):service.thumbnail(remote['id'],threading.Event())
    service.repo.enqueue('download',{'remote_id':remote['id']})
    assert service.run_one(threading.Event())['state']=='blocked'
    assert service.provider.http.calls==[]


def test_license_urls_and_plain_attribution():
    assert reuse_status('CC BY-SA 4.0','https://creativecommons.org/licenses/by-sa/4.0/','','Author')=='eligible'
    assert reuse_status('CC BY 4.0','https://creativecommons.org/licenses/by/4.0/','')=='review'
    assert reuse_status('CC BY-NC','https://creativecommons.org/licenses/by-nc/4.0/','','Author')=='review'
    assert reuse_status('CC0','https://creativecommons.org/publicdomain/zero/1.0/','')=='eligible'
    assert reuse_status('CC0','https://creativecommons.org/publicdomain/zero/1.0/','personality rights')=='review'
    assert plain('<a href="https://example.com">Author &amp; Co</a>')=='Author & Co'


@pytest.mark.parametrize('url',['http://upload.wikimedia.org/a','https://127.0.0.1/a','https://upload.wikimedia.org.evil.example/a','https://user:pass@upload.wikimedia.org/a','https://upload.wikimedia.org:8443/a'])
def test_remote_urls_reject_unrelated_hosts_credentials_and_ports(url):
    with pytest.raises(RemoteError):validate_url(url)


def test_commons_adapter_keeps_dates_license_and_continuation_separate(source):
    http=FakeHttp();provider=WikimediaProvider(http)
    http.json_response={'query':{'pages':[{'pageid':1234,'title':'File:Example.png','imageinfo':[dict(url=source['original_url'],thumburl=source['thumbnail_url'],descriptionurl=source['source_url'],width=64,height=96,size=200,mime='image/png',sha1='0'*40,timestamp='2026-01-01T00:00:00Z',extmetadata={'LicenseShortName':{'value':'CC BY-SA 4.0'},'LicenseUrl':{'value':'https://creativecommons.org/licenses/by-sa/4.0/'},'Artist':{'value':'<b>Author</b>'}})]}]},'continue':{'gcmcontinue':'next','continue':'gcmcontinue||'}}
    images,cursor=provider.photos('Example',None,threading.Event())
    assert cursor['gcmcontinue']=='next'
    assert images[0]['captured'] is None and images[0]['published']=='2026-01-01T00:00:00Z'
    assert images[0]['attribution']=='Author' and images[0]['license_status']=='eligible'
    assert http.calls[0][1]['gcmtype']=='file'
    provider.photos('Example',cursor|{'url':'evil'},threading.Event())
    assert 'url' not in http.calls[-1][1]


def referenced(value):return dict(rank='normal',references=[{'hash':'fixture'}],mainsnak=dict(snaktype='value',datavalue={'value':value}))


def test_auto_registration_only_for_referenced_unambiguous_metadata(db,settings):
    from app.artist_rules import GIRL_GROUP,K_POP
    candidate=dict(external_id='Q1234',kind='group',name='New Group',category='New Group',raw={'claims':{'P31':[referenced({'id':GIRL_GROUP})],'P136':[referenced({'id':K_POP})],'P571':[referenced({'time':'+2025-01-01T00:00:00Z'})]}})
    assert automatic_profile(candidate)=={'group_kind':'girl'}
    no_references=json.loads(json.dumps(candidate));no_references['raw']['claims']['P31'][0]['references']=[]
    assert automatic_profile(no_references) is None
    provider=FakeProvider();provider.entities=lambda ids,cancel:[candidate]
    service=OnlineService(db,settings,provider);approved(service.repo)
    service.repo.candidate(candidate)
    service.repo.enqueue('enrich',{'ids':['Q1234']},key='enrich:Q1234')
    result=service.run_one(threading.Event())
    assert result['library_changed']
    assert db.entities()['groups'][0]['kind']=='girl'
    assert service.repo.targets()[0]['external_id']=='Q1234'
    # A same-name different external identity stays pending and preserves the original.
    other=candidate|dict(external_id='Q5678')
    provider.entities=lambda ids,cancel:[other]
    service.repo.candidate(other);service.repo.enqueue('enrich',{'ids':['Q5678']},key='enrich:Q5678')
    service.run_one(threading.Event())
    assert len(db.entities()['groups'])==1
    assert any(x['external_id']=='Q5678' for x in service.repo.candidates())


def test_online_dialog_snapshot_and_closed_dialog_does_not_restart_workers(qtbot,db,settings,png,source):
    from PySide6.QtCore import QObject,Signal
    from app.online_dialog import OnlineDialog
    class Runner(QObject):
        changed=Signal()
        def tick(self,manual=False):pass
        def pause(self):pass
    service=OnlineService(db,settings,FakeProvider(png))
    _,target_id=target(service.repo)
    service.repo.index_images(target_id,[source],None)
    runner=Runner()
    dialog=OnlineDialog(service,runner,settings)
    qtbot.addWidget(dialog);dialog.show()
    qtbot.waitUntil(lambda:not dialog.snapshot_pending)
    assert dialog.images.rowCount()==1
    assert '정책' in dialog.status.text()
    dialog.images.selectRow(0)
    qtbot.waitUntil(lambda:'정책' in dialog.preview.text())
    assert service.provider.http.calls==[]
    dialog.close()
    runner.changed.emit()
    assert dialog.closed and dialog.tasks.stopped


def test_background_online_work_keeps_gui_event_loop_running(qtbot,settings):
    from PySide6.QtCore import QTimer
    from app.online_runner import OnlineRunner
    started=threading.Event();release=threading.Event()
    def work(cancel,queue='network'):
        if queue=='analysis':return None
        started.set();release.wait(2);return None
    repo=SimpleNamespace(recover=lambda:None,reviewed=lambda:False,schedule=lambda:None)
    service=SimpleNamespace(repo=repo,run_one=work)
    runner=OnlineRunner(service,settings)
    runner.tick(manual=True)
    qtbot.waitUntil(started.is_set)
    ticks=[];timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:ticks.append(1));timer.start()
    qtbot.waitUntil(lambda:len(ticks)>=3)
    assert runner.running
    release.set();qtbot.waitUntil(lambda:not runner.running)
    timer.stop();runner.stop()


def test_viewer_shutdown_releases_display_image_not_only_cache(qtbot,db,photo):
    from app.viewer import Viewer
    from app.images import inspect_image
    db.register(inspect_image(photo))
    rows,total=db.query()
    viewer=Viewer(rows,0,total=total)
    qtbot.addWidget(viewer);viewer.show()
    qtbot.waitUntil(lambda:viewer.canvas.item is not None)
    viewer.close()
    assert viewer.canvas.item is None
    assert viewer.canvas.scene().items()==[]
    assert viewer.cache.bytes_used==0


def test_new_file_revisions_have_distinct_remote_identifiers():
    http=FakeHttp();provider=WikimediaProvider(http)
    def response(sha1):
        return {'query':{'pages':[{'pageid':1234,'title':'File:Example.png','imageinfo':[dict(url='https://upload.wikimedia.org/example.png',width=64,height=96,size=200,mime='image/png',sha1=sha1,timestamp='2026-01-01T00:00:00Z',extmetadata={})]}]}}
    http.json_response=response('a'*40)
    first,_=provider.photos('Example',None,threading.Event())
    http.json_response=response('b'*40)
    second,_=provider.photos('Example',None,threading.Event())
    assert first[0]['source_id']!=second[0]['source_id']


def test_source_refresh_is_a_proposal_not_overwrite_of_local_memberships(db):
    repo=OnlineRepository(db)
    group,watch=target(repo,kind='group',name='My Group')
    member=db.add_entity('person','My Member',gender='male',group_id=group,gallery_target=False)
    repo.update_target(watch,dict(external_id='Q1234',name='Changed Upstream Name',category='New Category',members=['Q9999']))
    assert db.entities()['groups'][0]['name']=='My Group'
    assert db.members(group)[0]['id']==member
    assert db.members(group)[0]['gallery_target']==0
    assert repo.targets()[0]['category']=='New Category'
    assert any(x['external_id']=='Q1234' and x['status']=='pending' for x in repo.candidates())
