import json
import random
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.database import Database,SCHEMA
from app.online_repository import OnlineRepository
from app.source_repository import SourceRepository
from app.perceptual import bands,store_feature,candidates
from app.providers.http import RemoteError,Cancelled
from app.request_pacing import HostPacer


def pages(db,count=2):
    source=SourceRepository(db);ids=[]
    for i in range(count):
        source.save_spec(dict(id=f'owned{i}',name=f'Owned fixture {i}',category='user',adapter='html',domains=[f'owned{i}.example']))
        ids.append(source.add_page(f'owned{i}',f'https://owned{i}.example/page'))
    return ids


def test_latest_work_does_not_accumulate_behind_native_task(qtbot):
    from app.workers import Tasks
    tasks=Tasks(threads=1);release=threading.Event();started=threading.Event();executed=[];delivered=[]
    main_thread=threading.get_ident()
    def slow(cancelled):started.set();release.wait(3)
    tasks.submit(slow,lambda _:None,pytest.fail)
    qtbot.waitUntil(started.is_set)
    for i in range(3000):
        tasks.submit(lambda _,i=i:executed.append(i) or i,lambda result:delivered.append((result,threading.get_ident())),pytest.fail,key='latest-search')
    assert len(tasks.jobs)==2 and len(tasks.pending)==1 and len(tasks.active)==1
    release.set();qtbot.waitUntil(lambda:not tasks.jobs)
    assert executed==[2999] and delivered==[(2999,main_thread)]
    tasks.stop()


def test_explicit_writes_are_not_coalesced_and_cancelled_queue_never_runs(qtbot):
    from app.workers import Tasks
    tasks=Tasks(threads=1);gate=threading.Event();started=threading.Event();writes=[]
    tasks.submit(lambda _:(started.set(),gate.wait(3)),lambda _:None,pytest.fail)
    qtbot.waitUntil(started.is_set)
    obsolete=tasks.submit(lambda _:writes.append('obsolete'),lambda _:None,pytest.fail)
    obsolete.cancelled.set()
    for i in range(20):tasks.submit(lambda _,i=i:writes.append(i),lambda _:None,pytest.fail)
    gate.set();qtbot.waitUntil(lambda:not tasks.jobs)
    assert writes==list(range(20))
    tasks.stop()
    with pytest.raises(RuntimeError):tasks.submit(lambda _:None,lambda _:None,pytest.fail)


def test_source_claim_isolation_fairness_and_stale_token(db):
    now=[1000.];repo=OnlineRepository(db,lambda:now[0]);a,b=pages(db)
    first=repo.enqueue('web_scan',{'page_id':a},key='first')
    second=repo.enqueue('web_scan',{'page_id':a},key='second')
    other=repo.enqueue('web_scan',{'page_id':b},key='other')
    one=repo.claim();two=repo.claim()
    assert one['source_key']!=two['source_key']
    assert repo.claim() is None
    repo.recover()
    assert not repo.finish(one['id'],'done',claim_token=one['claim_token'])
    again=repo.claim(identity=one['id']);assert again['claim_token']!=one['claim_token']
    assert repo.finish(again['id'],'queued',payload={'page_id':again['payload']['page_id'],'cursor':'checkpoint'},result={'page':1},claim_token=again['claim_token'])
    now[0]+=1
    # A different ready source gets a turn before a continuing source.
    ready=repo.claim();assert ready['source_key']!=again['source_key']
    assert {j['id'] for j in repo.jobs()}=={first,second,other}


def test_source_retry_wait_survives_restart_and_manual_retry(db):
    now=[1000.];repo=OnlineRepository(db,lambda:now[0]);a,b=pages(db)
    job=repo.enqueue('web_scan',{'page_id':a});other=repo.enqueue('web_scan',{'page_id':b})
    claim=repo.claim(identity=job)
    repo.finish(job,'retry',delay=90,claim_token=claim['claim_token'])
    repo=OnlineRepository(db,lambda:now[0]);repo.retry(job)
    assert repo.claim(identity=job) is None
    assert repo.claim()['id']==other
    now[0]+=90
    assert repo.claim(identity=job)['id']==job


def test_band_index_recall_pagination_revision_checks_and_cancellation(db):
    page=pages(db,1)[0];rnd=random.Random(2026);seed=rnd.getrandbits(64);hashes=[]
    for i in range(900):
        value=seed
        if i<160:
            for bit in rnd.sample(range(64),i%5):value^=1<<bit
        else:value=rnd.getrandbits(64)
        hashes.append(f'{value:016x}')
    with db.connect() as conn:
        conn.executemany("INSERT INTO web_images(id,page_id,source_id,url,logical_key,sha256,dhash,provenance,updated) VALUES(?,?,'owned0',?,'fixture',?,?, '{}',0)",((f'{i:05}',page,f'https://owned0.example/{i}',f'{i:064x}',value) for i,value in enumerate(hashes)))
        for i,value in enumerate(hashes):store_feature(conn,f'{i:05}',f'{i:064x}',value)
        found={};after=''
        while True:
            batch,more=candidates(conn,'not-a-seed','seed-sha',f'{seed:016x}',threading.Event(),after,limit=37)
            assert len(batch)<=37
            assert not set(found)&{r['id'] for r in batch}
            found.update({r['id']:r['dhash'] for r in batch})
            if not more:break
            after=batch[-1]['id']
        truth={f'{i:05}' for i,value in enumerate(hashes) if (seed^int(value,16)).bit_count()<=4}
        assert truth<=set(found)
        filtered={i for i,value in found.items() if (seed^int(value,16)).bit_count()<=4}
        assert filtered==truth
        conn.execute("UPDATE web_images SET sha256='new-revision' WHERE id='00000'")
        batch,_=candidates(conn,'unused','seed-sha',hashes[0],threading.Event())
        assert '00000' not in {r['id'] for r in batch}
        cancel=threading.Event();cancel.set()
        with pytest.raises(Cancelled):candidates(conn,'x','sha',hashes[0],cancel)
    with pytest.raises(ValueError):bands('not-a-hash')


def test_uniform_hash_index_pages_all_candidates_beyond_old_cutoff(db):
    page=pages(db,1)[0];count=10020
    with db.connect() as conn:
        conn.executemany("INSERT INTO web_images(id,page_id,source_id,url,logical_key,sha256,dhash,provenance,updated) VALUES(?,?,'owned0',?,'fixture',?,'0000000000000000','{}',0)",((f'{i:06}',page,f'https://owned0.example/{i}',f'{i:064x}') for i in range(count)))
        for i in range(count):store_feature(conn,f'{i:06}',f'{i:064x}','0000000000000000')
        after='';seen=[]
        while True:
            rows,more=candidates(conn,'unused','sha','0000000000000000',threading.Event(),after)
            seen.extend(row['id'] for row in rows)
            if not more:break
            after=rows[-1]['id']
    assert len(seen)==count and len(set(seen))==count and seen[-1]=='010019'


def test_shared_host_pacing_cooldown_does_not_block_another_host():
    now=[10.];pacer=HostPacer(lambda:now[0]);cancel=threading.Event()
    pacer.wait('same.example',1,cancel);pacer.cooldown('same.example',90)
    with pytest.raises(RemoteError) as error:pacer.wait('same.example',1,cancel)
    assert error.value.retry_after==90
    pacer.wait('other.example',1,cancel)
    now[0]+=90;pacer.wait('same.example',1,cancel)
    cancel.set()
    with pytest.raises(Cancelled):pacer.wait('fresh.example',1,cancel)


def test_independent_clients_and_redirects_share_host_budget():
    from app.providers.http import HttpClient,SafeRedirect
    from urllib.request import Request
    a=HttpClient();b=HttpClient();assert a.pacer is b.pacer
    now=[100.];pacer=HostPacer(lambda:now[0]);cancel=threading.Event()
    request=Request('https://commons.wikimedia.org/a');request.archive_cancelled=cancel
    redirect=SafeRedirect(pacer=pacer,interval=1)
    redirected=redirect.redirect_request(request,None,302,'found',{},'https://commons.wikimedia.org/b')
    assert redirected.archive_cancelled is cancel
    pacer.cooldown('commons.wikimedia.org',30)
    with pytest.raises(RemoteError):redirect.redirect_request(request,None,302,'found',{},'https://commons.wikimedia.org/b')


def test_live_library_lock_and_killed_process_recovery(tmp_path,qtbot):
    from app.instance import LibraryInstance
    ready=tmp_path/'ready'
    code="from app.instance import LibraryInstance; from pathlib import Path; import sys,time; lock=LibraryInstance(sys.argv[1]).acquire(); Path(sys.argv[2]).write_text('ready'); time.sleep(30)"
    process=subprocess.Popen([sys.executable,'-c',code,str(tmp_path),str(ready)])
    try:
        qtbot.waitUntil(ready.exists,timeout=5000)
        with pytest.raises(RuntimeError):LibraryInstance(tmp_path).acquire()
        process.kill();process.wait(timeout=5)
        with LibraryInstance(tmp_path):
            with pytest.raises(RuntimeError):LibraryInstance(tmp_path).acquire()
        with LibraryInstance(tmp_path):pass
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)


def test_two_network_sources_and_analysis_can_progress_without_blocking_gui(qtbot,db,settings):
    from PySide6.QtCore import QTimer
    from app.online_runner import OnlineRunner
    settings.values['online_enabled']=False
    repo=OnlineRepository(db);a,b=pages(db)
    ids=[repo.enqueue('web_scan',{'page_id':a}),repo.enqueue('web_scan',{'page_id':b})]
    analysis=repo.enqueue('web_analyze',{'item_id':'owned-analysis'})
    entered=set();lock=threading.Lock();release=threading.Event()
    class Backend:
        def __init__(self):self.repo=repo
        def run_one(self,cancelled,queue='network',job_id=None):
            job=repo.claim(queue,job_id)
            if not job:return None
            if self.on_job_started:self.on_job_started(queue)
            if queue=='network':
                with lock:entered.add(job['source_key'])
                while not release.wait(.01):
                    if cancelled.is_set():break
            repo.finish(job['id'],'done',claim_token=job['claim_token'])
            return dict(id=job['id'],state='done',library_changed=False)
    runner=OnlineRunner(Backend(),settings);runner.timer.stop()
    try:
        for identity in ids:runner.request(identity)
        qtbot.waitUntil(lambda:len(entered)==2)
        runner.request(analysis,'analysis');qtbot.waitUntil(lambda:repo.job_state(analysis)=='done')
        ticks=[];timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:ticks.append(1));timer.start()
        qtbot.waitUntil(lambda:len(ticks)>=4)
        assert runner.running and len(runner.flights)<=3
        timer.stop();release.set();qtbot.waitUntil(lambda:not runner.running)
    finally:release.set();runner.stop()


def test_foreground_yields_automatic_work_but_preserves_targeted_request(qtbot,settings):
    from app.online_runner import OnlineRunner
    calls=[]
    class Backend:
        def __init__(self):self.repo=SimpleNamespace(recover=lambda:None,reviewed=lambda:False,schedule=lambda:None)
        def run_one(self,cancelled,queue='network',job_id=None):
            calls.append((queue,job_id));return dict(id=job_id,state='done',library_changed=False) if job_id else None
    runner=OnlineRunner(Backend(),settings);runner.timer.stop()
    try:
        with runner.foreground():
            with runner.foreground():runner.tick();assert not runner.flights
            assert runner.foreground_busy
            runner.request('explicit-only','analysis')
            qtbot.waitUntil(lambda:not runner.analysis_running)
            assert calls==[('analysis','explicit-only')]
        runner.defer(30);runner.tick();assert not runner.flights
        runner.quiet_until=0;runner.tick();qtbot.waitUntil(lambda:not runner.flights)
        assert any(job is None for _,job in calls)
    finally:runner.stop()


def test_v4_upgrade_backfills_without_image_reads_and_rolls_back_on_failure(tmp_path,monkeypatch):
    from app.online_schema import SCHEMA_V2
    from app.source_schema import SCHEMA_V3
    from app.related_schema import SCHEMA_V4
    from app.source_specs import SourceSpec
    import app.stability_schema as schema
    path=tmp_path/'old-v4.db'
    spec=SourceSpec.load(dict(id='owned',name='Owned fixture',category='user',adapter='html',domains=['owned.example']))
    with sqlite3.connect(path) as c:
        for script in (SCHEMA,SCHEMA_V2,SCHEMA_V3,SCHEMA_V4):c.executescript(script)
        c.execute('INSERT INTO source_profiles VALUES(?,?,?,?,?,?,?,?)',('owned',spec.name,spec.category,spec.adapter,json.dumps(spec.dictionary()),spec.digest,1,0))
        c.execute("INSERT INTO source_pages(id,source_id,url) VALUES('page','owned','https://owned.example/page')")
        c.execute("INSERT INTO web_images(id,page_id,source_id,url,logical_key,sha256,dhash,provenance,updated) VALUES('legacy','page','owned','https://owned.example/photo','img','sha','0123456789abcdef','{}',0)")
        c.execute("INSERT INTO resource_permissions(resource_id,operation,state,evidence,checked) VALUES('web:legacy','local_analysis','denied','{}',0)")
        c.execute("INSERT INTO sync_jobs(id,job_key,kind,payload,updated) VALUES('job','job','web_scan','{\"page_id\":\"page\"}',0)")
        c.execute('PRAGMA user_version=4')
    original=schema.SCHEMA_V5;monkeypatch.setattr(schema,'SCHEMA_V5',original+'; INVALID SQL;')
    with pytest.raises(sqlite3.Error):Database(path)
    with sqlite3.connect(path) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==4
        assert 'source_key' not in {row[1] for row in c.execute('PRAGMA table_info(sync_jobs)')}
    assert len(list(tmp_path.glob('*.v4-*.bak')))==1
    monkeypatch.setattr(schema,'SCHEMA_V5',original);db=Database(path)
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM perceptual_features').fetchone()[0]==1
        assert c.execute('SELECT source_key FROM sync_jobs').fetchone()[0]=='web:owned'
        assert c.execute('SELECT state FROM resource_permissions').fetchone()[0]=='denied'
        assert c.execute('PRAGMA user_version').fetchone()[0]==5
    Database(path);assert len(list(tmp_path.glob('*.v4-*.bak')))==2


def populate_features(service,page,hash_value,count,near_last=False):
    with service.db.connect() as c:
        for i in range(count):
            identity=f'noise-{i:06}';sha=f'{i+100:064x}'
            c.execute("INSERT INTO web_images(id,page_id,source_id,url,logical_key,sha256,dhash,provenance,updated) VALUES(?,?,'owned',?,'fixture',?,?,'{}',0)",(identity,page,f'https://cdn.example/{identity}.png',sha,hash_value))
            store_feature(c,identity,sha,hash_value)
            c.execute("INSERT INTO resource_permissions(resource_id,operation,state,evidence,checked) VALUES(?,'local_analysis','allowed','{}',0)",('web:'+identity,))


# Reuse the isolated, owned fixture provider; these tests never request real URLs.
from test_sources import web,indexed,permit,image_bytes


def prepared_analysis(web):
    service,client,resources=web;page,item=indexed(web)
    resources[item['url']]=(image_bytes((80,120)),'image/png')
    for operation in ('original_download','local_analysis'):
        permit(service.repo,operation);permit(service.repo,operation,'web:'+item['id'])
    service.probe(item['id'],threading.Event());service.analyze(item['id'],threading.Event())
    return service,page,service.repo.image(item['id'])


def test_analysis_checkpoint_resumes_without_redecoding_and_finds_late_match(web,settings,monkeypatch):
    from app.online_service import OnlineService
    import app.web_service as module
    service,page,item=prepared_analysis(web)
    # All noise shares one band but differs by 51 bits, exercising several SQL
    # candidate pages while exact-distance checks reject it.
    noise=f"{int(item['dhash'],16)^(((1<<51)-1)<<13):016x}"
    populate_features(service,page,noise,600)
    with service.db.connect() as c:
        c.execute("INSERT INTO web_images(id,page_id,source_id,url,logical_key,sha256,dhash,provenance,updated) VALUES('zz-last',?,'owned','https://cdn.example/late.png','late','late-sha',?,'{}',0)",(page,item['dhash']))
        store_feature(c,'zz-last','late-sha',item['dhash'])
        c.execute("INSERT INTO resource_permissions(resource_id,operation,state,evidence,checked) VALUES('web:zz-last','local_analysis','allowed','{}',0)")
    backend=OnlineService(service.db,settings,None);backend.web=service
    identity=backend.repo.enqueue('web_analyze',{'item_id':item['id']})
    assert backend.run_one(threading.Event(),'analysis')['state']=='queued'
    first=next(j for j in backend.repo.jobs() if j['id']==identity)
    checkpoint=json.loads(first['payload']);assert checkpoint['after'] and checkpoint['analysis_sha']==item['sha256']
    backend=OnlineService(service.db,settings,None);backend.web=service;backend.repo.recover()
    monkeypatch.setattr(module,'file_hash',lambda _:(_ for _ in ()).throw(AssertionError('Continuation must reuse verified hash features')))
    assert backend.run_one(threading.Event(),'analysis')['state']=='done'
    result=json.loads(next(j for j in backend.repo.jobs() if j['id']==identity)['result'])
    assert result['possible_duplicates']==1 and not result['match_limit_reached']
    assert any(pair['right_id']=='zz-last' or pair['left_id']=='zz-last' for pair in service.repo.matches(item['id']))
    # A revision change invalidates the saved continuation instead of comparing
    # old features as though they described the new file.
    with service.db.connect() as c:c.execute("UPDATE web_images SET sha256='changed' WHERE id=?",(item['id'],))
    with pytest.raises(RemoteError):service.analyze(item['id'],threading.Event(),checkpoint['after'],checkpoint['analysis_sha'])


def test_low_information_match_explosion_is_explicitly_capped(web):
    service,page,item=prepared_analysis(web)
    populate_features(service,page,item['dhash'],230)
    result=service.analyze(item['id'],threading.Event())
    assert result['possible_duplicates']==200 and result['match_limit_reached'] and result['next_cursor'] is None
    with service.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM image_relations').fetchone()[0]==200
        assert c.execute('SELECT COUNT(*) FROM web_images').fetchone()[0]==231
        assert c.execute('SELECT COUNT(*) FROM assets').fetchone()[0]==0


def test_simulated_multi_day_failures_crashes_and_cursors_preserve_local_data(db,settings,photo):
    from app.online_service import OnlineService
    from app.images import inspect_image
    asset=db.register(inspect_image(photo))[0];db.favorite(asset,True)
    now=[1000.];page_ids=pages(db,4);backend=OnlineService(db,settings,None,lambda:now[0])
    for i in range(240):backend.repo.enqueue('web_scan',{'page_id':page_ids[i%4],'fixture_index':i},key=f'stress:{i}')
    failed_once=set();cancelled_once=set();completed=set();restarts=0
    def execute(job,cancelled):
        index=job['payload']['fixture_index'];page=job['payload'].get('page',0)
        if index%19==0 and index not in failed_once:
            failed_once.add(index);raise RemoteError('Synthetic HTTP 429',retryable=True,retry_after=90)
        if index%37==0 and index not in cancelled_once:
            cancelled_once.add(index);raise Cancelled()
        if page<2:return {'page':page},job['payload']|{'page':page+1}
        completed.add(index);return {'complete':True},None
    for iteration in range(1800):
        now[0]+=900
        if iteration%53==0:
            backend.repo.claim()  # Simulated process loss before checkpoint commit.
            backend=OnlineService(db,settings,None,lambda:now[0]);backend.repo.recover();restarts+=1
        backend.execute=execute;backend.run_one(threading.Event())
        if len(completed)==240:break
    assert len(completed)==240 and failed_once and cancelled_once and restarts>10
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM sync_jobs WHERE state='done'").fetchone()[0]==240
        assert c.execute("SELECT COUNT(*) FROM sync_jobs WHERE claim_token IS NOT NULL").fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM assets').fetchone()[0]==1
        assert c.execute('SELECT favorite FROM assets WHERE id=?',(asset,)).fetchone()[0]==1
    assert now[0]-1000>=7*24*3600
    assert Path(photo).is_file()


def test_finished_and_pruned_jobs_release_signal_closures(qtbot):
    import gc,weakref
    from app.workers import Tasks
    tasks=Tasks(threads=1);gate=threading.Event();started=threading.Event()
    tasks.submit(lambda _:(started.set(),gate.wait(3)),lambda _:None,pytest.fail)
    qtbot.waitUntil(started.is_set)
    refs=[]
    for i in range(1000):
        job=tasks.submit(lambda _:None,lambda _:None,pytest.fail,key='replaceable')
        refs.append(weakref.ref(job))
    del job
    gc.collect()
    assert sum(ref() is not None for ref in refs)<=1
    gate.set();qtbot.waitUntil(lambda:not tasks.jobs)
    gc.collect()
    assert not any(ref() is not None for ref in refs)
    tasks.stop()


def test_parallel_registration_reuses_one_asset_and_preserves_both_links(db,photo):
    from concurrent.futures import ThreadPoolExecutor
    from app.images import inspect_image
    one=db.add_entity('person','A');two=db.add_entity('person','B')
    metadata=inspect_image(photo);barrier=threading.Barrier(2)
    def register(person):barrier.wait(timeout=3);return db.register(metadata,person=person)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(register,(one,two)))
    assert results[0][0]==results[1][0]
    assert sorted(duplicate for _,duplicate in results)==[False,True]
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM assets').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM image_person').fetchone()[0]==2
    assert photo.is_file()


def test_http_clients_enforce_spacing_without_real_network():
    import io
    from concurrent.futures import ThreadPoolExecutor
    from app.providers.http import HttpClient
    pacer=HostPacer();clients=[HttpClient(interval=.025,allowed_hosts={'fixture.example'},pacer=pacer) for _ in range(2)]
    times=[];lock=threading.Lock();barrier=threading.Barrier(2)
    class Opener:
        def open(self,request,timeout):
            with lock:times.append(time.monotonic())
            return io.BytesIO(b'fixture')
    for client in clients:client.opener=Opener()
    def request(client):
        barrier.wait(timeout=3)
        with client.open('https://fixture.example/image',threading.Event()) as response:assert response.read()==b'fixture'
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(request,clients))
    assert abs(times[1]-times[0])>=.024


def test_http_retry_after_is_shared_before_another_client_sends():
    from app.providers.http import HttpClient
    from urllib.error import HTTPError
    now=[100.];pacer=HostPacer(lambda:now[0]);calls=[]
    class Rejected:
        def open(self,request,timeout):
            calls.append(request.full_url)
            raise HTTPError(request.full_url,429,'Fixture limit',{'Retry-After':'90'},None)
    a=HttpClient(allowed_hosts={'fixture.example'},pacer=pacer);b=HttpClient(allowed_hosts={'fixture.example'},pacer=pacer)
    a.opener=Rejected();b.opener=Rejected()
    with pytest.raises(RemoteError) as first:a.open('https://fixture.example/a',threading.Event())
    with pytest.raises(RemoteError) as second:b.open('https://fixture.example/b',threading.Event())
    assert first.value.retry_after==90 and second.value.retry_after==90 and len(calls)==1


def test_reduced_network_budget_retains_manual_work_until_slot_is_free(qtbot,settings):
    from app.online_runner import OnlineRunner
    settings.values.update(online_enabled=False,network_workers=1)
    entered=[];release=threading.Event()
    class Backend:
        def __init__(self):self.repo=SimpleNamespace(recover=lambda:None)
        def run_one(self,cancelled,queue='network',job_id=None):
            entered.append(job_id)
            while not release.wait(.005):
                if cancelled.is_set():break
            return dict(id=job_id,state='done',library_changed=False)
    runner=OnlineRunner(Backend(),settings);runner.timer.stop()
    try:
        runner.request('one');qtbot.waitUntil(lambda:entered==['one'])
        runner.request('two');assert len(runner.flights)==1 and list(runner.requests['network'])==['two']
        release.set();qtbot.waitUntil(lambda:entered==['one','two'] and not runner.running)
        assert not runner.requests['network']
    finally:release.set();runner.stop()
