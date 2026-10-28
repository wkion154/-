"""Offscreen real-image loading responsiveness check, synthetic licensed-by-author fixtures."""
import os,sys,tempfile,time,json,resource
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.database import Database
from app.core import Settings
from app.images import import_paths
from app.main_window import MainWindow
from app.theme import STYLE
import threading

app=QApplication([])
app.setStyleSheet(STYLE)
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder)
    photos=root/'photos';photos.mkdir()
    for i in range(100):
        image=Image.new('RGB',(2400,3600),(i*19%256,i*37%256,i*47%256))
        ImageDraw.Draw(image).text((50,50),f'SYNTHETIC PERFORMANCE FIXTURE {i}',fill='white')
        image.save(photos/f'photo-{i:03}.jpg',quality=90)
    db=Database(root/'library.db')
    begin=time.perf_counter()
    report=import_paths(db,[photos],root/'cache',root/'originals',False,None,None,threading.Event())
    import_ms=(time.perf_counter()-begin)*1000
    settings=Settings(root/'user')
    w=MainWindow(db,settings)
    gaps=[];last=[time.perf_counter()]
    def tick():
        now=time.perf_counter();gaps.append((now-last[0])*1000);last[0]=now
    timer=QTimer();timer.setInterval(10);timer.timeout.connect(tick);timer.start()
    w.resize(1440,920)
    begin=time.perf_counter();w.show()
    deadline=time.perf_counter()+12
    first=None
    while time.perf_counter()<deadline:
        app.processEvents()
        if w.gallery.thumbs and first is None:first=(time.perf_counter()-begin)*1000
        if len(w.gallery.thumbs)>=9:break
        time.sleep(.001)
    for _ in range(10):
        w.gallery.verticalScrollBar().setValue(w.gallery.verticalScrollBar().maximum())
        until=time.perf_counter()+.1
        while time.perf_counter()<until:app.processEvents();time.sleep(.001)
    timer.stop()
    result={'scope':'Linux offscreen Qt, 100 synthetic 2400x3600 JPEGs. Timer gaps are event-loop responsiveness observations, not physical display frame times.', 'import':report,'import_total_ms':round(import_ms,2),'first_thumbnail_ms':round(first,2) if first else None,'event_loop_max_gap_ms':round(max(gaps),2) if gaps else None,'event_loop_p95_gap_ms':round(sorted(gaps)[int(len(gaps)*.95)],2) if gaps else None,'peak_process_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2),'thumbnail_memory_count':len(w.gallery.thumbs),'queued_thumbnails':len(w.gallery.pending)}
    w.close()
    print(json.dumps(result,indent=2))
