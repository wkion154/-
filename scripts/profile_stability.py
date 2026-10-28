"""Repeatable synthetic index, worker-retention and foreground contention measurements."""
import os,sys,json,tempfile,time,threading,random,gc,weakref,resource
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.database import Database
from app.source_repository import SourceRepository
from app.perceptual import store_feature,candidates
from app.workers import Tasks
from app.main_window import MainWindow
from app.online_runner import OnlineRunner
from app.online_repository import OnlineRepository
from app.core import Settings


def pump(predicate,seconds=15):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return
        time.sleep(.001)
    raise TimeoutError('Fixture did not become ready')

app=QApplication([]);report={}
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);db=Database(root/'library.db');sources=SourceRepository(db)
    source_pages=[]
    for i in range(2):
        sources.save_spec(dict(id=f'fixture{i}',name=f'Owned fixture {i}',category='user',adapter='html',domains=[f'fixture{i}.example']))
        source_pages.append(sources.add_page(f'fixture{i}',f'https://fixture{i}.example/page'))
    rng=random.Random(606);seed=0x38c5a726d9e1b043;previous=0;index_results=[]
    for count in (10000,100000):
        start=time.perf_counter()
        with db.connect() as conn:
            for i in range(previous,count):
                bits=seed^(1<<(i%64)) if i>=count-10 else rng.getrandbits(64)
                value=f'{bits:016x}';identity=f'{i:08}';sha=f'{i:064x}'
                conn.execute("INSERT INTO web_images(id,page_id,source_id,url,logical_key,sha256,dhash,provenance,updated) VALUES(?,?,'fixture0',?,'fixture',?,?,'{}',0)",(identity,source_pages[0],f'https://fixture0.example/{i}.png',sha,value))
                store_feature(conn,identity,sha,value)
        build_ms=(time.perf_counter()-start)*1000;previous=count
        with db.connect() as conn:
            start=time.perf_counter();after='';seen={};page_count=0
            while True:
                rows,more=candidates(conn,'unregistered-seed','fixture',f'{seed:016x}',threading.Event(),after)
                page_count+=1;seen.update({r['id']:r['dhash'] for r in rows})
                if not more:break
                after=rows[-1]['id']
            search_ms=(time.perf_counter()-start)*1000
            truth={r['id'] for r in conn.execute('SELECT id,dhash FROM web_images') if (seed^int(r['dhash'],16)).bit_count()<=4}
            exact={identity for identity,value in seen.items() if (seed^int(value,16)).bit_count()<=4}
            assert exact==truth
        index_results.append(dict(indexed=count,incremental_build_ms=round(build_ms,2),query_ms=round(search_ms,2),candidate_count=len(seen),pages=page_count,exact_matches=len(exact),bruteforce_matches=len(truth)))
    report['band_index']=index_results
    tasks=Tasks(threads=1);gate=threading.Event();started=threading.Event();executed=[];refs=[]
    tasks.submit(lambda _:(started.set(),gate.wait(5)),lambda _:None,lambda message:(_ for _ in ()).throw(RuntimeError(message)))
    pump(started.is_set);start=time.perf_counter()
    for i in range(10000):
        job=tasks.submit(lambda _,i=i:executed.append(i),lambda _:None,lambda _:None,key='latest')
        refs.append(weakref.ref(job))
    del job
    submit_ms=(time.perf_counter()-start)*1000;retained=len(tasks.jobs);pending=len(tasks.pending)
    gc.collect();alive_pending=sum(ref() is not None for ref in refs)
    gate.set();pump(lambda:not tasks.jobs);gc.collect()
    alive_finished=sum(ref() is not None for ref in refs);tasks.stop()
    assert executed==[9999] and retained==2 and alive_pending==1 and alive_finished==0
    report['obsolete_work']=dict(submissions=10000,submission_loop_ms=round(submit_ms,2),retained_jobs_while_blocked=retained,pending=pending,executed_latest_requests=len(executed),live_replaced_jobs=alive_pending,live_jobs_after_completion=alive_finished)
    photo=root/'owned-generated.jpg';Image.new('RGB',(640,960),'#7983a0').save(photo)
    with db.connect() as conn:
        conn.executemany('INSERT INTO assets(id,path,name,sha256,width,height,size,format,registered) VALUES(?,?,?,?,?,?,?,?,?)',((str(i),str(photo),f'Photo {i:06}',f'{i:064x}',640,960,photo.stat().st_size,'JPEG',i) for i in range(100000)))
    settings=Settings(root/'user');settings.values['online_enabled']=False
    window=MainWindow(db,settings);window.resize(1440,920);window.show()
    pump(lambda:window.model.total==100000 and bool(window.gallery.thumbs))
    pump(lambda:window.gallery.visualRect(window.model.index(99999)).isValid())
    original_runner=window.online_runner;original_runner.stop()
    repo=OnlineRepository(db);entered=set();guard=threading.Lock();max_network=[0];active_network=[0]
    class FixtureBackend:
        def __init__(self):self.repo=repo
        def run_one(self,cancelled,queue='network',job_id=None):
            job=repo.claim(queue,job_id)
            if not job:return None
            if self.on_job_started:self.on_job_started(queue)
            with guard:
                entered.add(queue+job['source_key'])
                if queue=='network':
                    active_network[0]+=1;max_network[0]=max(max_network[0],active_network[0])
            deadline=time.monotonic()+.65
            while time.monotonic()<deadline and not cancelled.is_set():
                if queue=='analysis':
                    with db.connect() as conn:candidates(conn,'seed','fixture',f'{seed:016x}',cancelled)
                cancelled.wait(.003)
            if queue=='network':
                with guard:active_network[0]-=1
            repo.finish(job['id'],'done',claim_token=job['claim_token'])
            return dict(id=job['id'],state='done',library_changed=False)
    runner=OnlineRunner(FixtureBackend(),settings,window);runner.timer.stop();window.online_runner=runner
    for page in source_pages:runner.request(repo.enqueue('web_scan',{'page_id':page}))
    runner.request(repo.enqueue('web_analyze',{'item_id':'fixture-only'}),'analysis')
    pump(lambda:len(entered)==3)
    gaps=[];last=[time.perf_counter()]
    def tick():
        now=time.perf_counter();gaps.append((now-last[0])*1000);last[0]=now
    timer=QTimer();timer.setInterval(10);timer.timeout.connect(tick);timer.start()
    start=time.perf_counter();max_metadata=0
    for position in range(0,100000,5000):
        index=window.model.index(position)
        window.gallery.scrollTo(index)
        pump(lambda position=position:window.model.row_at(position,request=False) is not None)
        max_metadata=max(max_metadata,len(window.model.rows))
    navigation_ms=(time.perf_counter()-start)*1000
    pump(lambda:not runner.flights)
    timer.stop();assert gaps and max_metadata<=2048 and max_network[0]==2
    report['foreground_under_fixture_load']=dict(metadata_count=100000,network_slots_observed=max_network[0],analysis_slots=1,viewport_jumps=20,navigation_total_ms=round(navigation_ms,2),event_loop_max_gap_ms=round(max(gaps),2),event_loop_p95_gap_ms=round(sorted(gaps)[int(len(gaps)*.95)],2),max_resident_metadata=max_metadata,peak_process_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2))
    window.close();app.processEvents()
print(json.dumps(dict(scope='Linux Qt offscreen. Precomputed synthetic hashes and metadata; one generated JPEG for virtual photos. Fixture network workers wait locally; no external requests or real AI. Tests responsiveness and specific worker retention, not physical display FPS, all-process leak freedom or real-site uptime.',**report),indent=2))
