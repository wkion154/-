"""Exercise actual Qt virtual gallery navigation, not just SQL, at 10k and 100k rows."""
import os,sys,tempfile,time,json,resource
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.database import Database
from app.core import Settings
from app.main_window import MainWindow
from app.theme import STYLE


def pump_until(app,predicate,timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return
        time.sleep(.001)
    raise TimeoutError('Virtual gallery readiness failed')

app=QApplication([]);app.setStyleSheet(STYLE)
results=[]
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    photo=root/'fixture.jpg'
    Image.new('RGB',(640,960),'#717c95').save(photo)
    db=Database(root/'library.sqlite3')
    previous=0
    for count in (10000,100000):
        with db.connect() as conn:
            conn.executemany('INSERT INTO assets(id,path,name,sha256,width,height,size,format,registered) VALUES(?,?,?,?,?,?,?,?,?)',[(str(i),str(photo),f'photo-{i:06}.jpg',f'{i:064x}',640,960,10000,'JPEG',i) for i in range(previous,count)])
        previous=count
        w=MainWindow(db,Settings(root/f'user-{count}'))
        w.resize(1440,920)
        gaps=[];last=[time.perf_counter()]
        def tick():
            now=time.perf_counter();gaps.append((now-last[0])*1000);last[0]=now
        timer=QTimer();timer.setInterval(10);timer.timeout.connect(tick);timer.start()
        start=time.perf_counter();w.show()
        pump_until(app,lambda:w.model.total==count and bool(w.gallery.thumbs))
        first=(time.perf_counter()-start)*1000
        # Wait for batched geometry before scrolling to the final item.
        index=w.model.index(count-1)
        pump_until(app,lambda:w.gallery.visualRect(index).isValid())
        start=time.perf_counter()
        w.gallery.scrollTo(index)
        pump_until(app,lambda:w.model.row_at(count-1,request=False) is not None)
        last_page=(time.perf_counter()-start)*1000
        max_resident=len(w.model.rows)
        for position in range(0,count,max(1,count//12)):
            index=w.model.index(position)
            w.gallery.scrollTo(index)
            pump_until(app,lambda position=position:w.model.row_at(position,request=False) is not None)
            max_resident=max(max_resident,len(w.model.rows))
        timer.stop()
        assert max_resident<=2048
        assert len(w.gallery.pending)<=8
        results.append(dict(metadata_count=count,first_thumbnail_ms=round(first,2),last_page_navigation_ms=round(last_page,2),
            max_resident_metadata=max_resident,thumbnail_cache_count=len(w.gallery.thumbs),event_loop_max_gap_ms=round(max(gaps),2),
            event_loop_p95_gap_ms=round(sorted(gaps)[int(len(gaps)*.95)],2),peak_process_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2)))
        w.close()
        app.processEvents()
print(json.dumps({'scope':'Linux Qt offscreen actual QListView; synthetic metadata references one generated 640x960 JPEG with distinct asset hashes. This tests virtual positions/page residency, not 100k distinct photo decodes or physical monitor frame rate. Process RSS is cumulative high-water mark.','results':results},indent=2))
