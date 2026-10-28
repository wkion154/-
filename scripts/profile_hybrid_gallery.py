"""Actual Qt mixed local/online metadata gallery; no external traffic."""
import os,sys,tempfile,time,json,resource
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.database import Database
from app.core import Settings
from app.source_repository import SourceRepository
from app.main_window import MainWindow

app=QApplication([])
def pump(predicate,seconds=15):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        app.processEvents()
        if predicate():return
        time.sleep(.001)
    raise TimeoutError('Hybrid virtual gallery readiness failed')

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);db=Database(root/'library.db');settings=Settings(root/'user');settings.values['online_enabled']=False
    photo=root/'owned.jpg';Image.new('RGB',(640,960),'#8d81a0').save(photo)
    sources=SourceRepository(db);sources.save_spec(dict(id='fixture',name='Owned mixed-gallery fixture',category='user',adapter='html',domains=['fixture.example']))
    page=sources.add_page('fixture','https://fixture.example/page')
    with db.connect() as c:
        c.executemany('INSERT INTO assets(id,path,name,sha256,width,height,size,format,registered) VALUES(?,?,?,?,?,?,?,?,?)',((f'a{i:06}',str(photo),f'Owned {i}',f'{i:064x}',640,960,1000,'JPEG',i*2) for i in range(50000)))
        c.executemany("INSERT INTO web_images(id,page_id,source_id,url,logical_key,title,declared_width,declared_height,provenance,updated) VALUES(?,?,'fixture',?,'fixture',?,2400,3600,'{}',?)",((f'r{i:06}',page,f'https://fixture.example/{i}.jpg',f'Unmeasured remote {i}',i*2+1) for i in range(50000)))
    window=MainWindow(db,settings);window.resize(1440,920)
    gaps=[];last=[time.perf_counter()]
    def tick():
        now=time.perf_counter();gaps.append((now-last[0])*1000);last[0]=now
    timer=QTimer();timer.setInterval(10);timer.timeout.connect(tick);timer.start()
    start=time.perf_counter();window.show();pump(lambda:window.model.total==100000 and bool(window.gallery.thumbs))
    first_ms=(time.perf_counter()-start)*1000
    pump(lambda:window.gallery.visualRect(window.model.index(99999)).isValid())
    start=time.perf_counter();window.gallery.scrollTo(window.model.index(99999));pump(lambda:window.model.row_at(99999,False) is not None)
    last_ms=(time.perf_counter()-start)*1000;resident=len(window.model.rows)
    for position in range(0,100000,8000):
        window.gallery.scrollTo(window.model.index(position));pump(lambda position=position:window.model.row_at(position,False) is not None)
        resident=max(resident,len(window.model.rows))
    timer.stop();assert resident<=2048
    result=dict(local_metadata=50000,remote_metadata=50000,total=window.model.total,first_local_thumbnail_ms=round(first_ms,2),last_page_ms=round(last_ms,2),event_loop_max_gap_ms=round(max(gaps),2),event_loop_p95_gap_ms=round(sorted(gaps)[int(len(gaps)*.95)],2),max_resident_metadata=resident,thumbnail_jobs_limit=8,remote_original_requests=0,peak_process_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2))
    window.close()
print(json.dumps(dict(scope='Linux Qt offscreen: 50k local rows reference one generated JPEG with synthetic hashes; 50k online metadata links have no image permission and perform no requests. Not 100k distinct photo decodes, real-site collection, or physical display FPS.',results=result),indent=2))
