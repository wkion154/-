"""Real decoded-image neighbor reuse and UI event-loop observations, offscreen Qt."""
import os,sys,time,tempfile,json,resource
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from app.images import inspect_image
from app.viewer import Viewer
from app.theme import STYLE

app=QApplication([]);app.setStyleSheet(STYLE)

def pump(predicate,seconds=10):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return
        time.sleep(.001)
    raise TimeoutError('Viewer readiness timed out')

with tempfile.TemporaryDirectory() as directory:
    rows=[]
    for i in range(4):
        path=Path(directory)/f'photo-{i}.jpg'
        image=Image.new('RGB',(3840,2160),(i*55,120,190));image.save(path);del image
        rows.append(inspect_image(path)|dict(id=str(i),favorite=False))
    viewer=Viewer(rows,1)
    gaps=[];last=[time.perf_counter()]
    def tick():
        now=time.perf_counter();gaps.append((now-last[0])*1000);last[0]=now
    timer=QTimer();timer.setInterval(10);timer.timeout.connect(tick);timer.start()
    start=time.perf_counter();viewer.show()
    pump(lambda:viewer.displayed_id=='1')
    cold_ms=(time.perf_counter()-start)*1000
    pump(lambda:viewer.key(rows[0]) in viewer.cache.items and viewer.key(rows[2]) in viewer.cache.items)
    transitions=[]
    for delta in (1,-1,-1,1,1):
        target=viewer.position+delta
        start=time.perf_counter();viewer.step(delta)
        pump(lambda:viewer.displayed_id==str(target))
        transitions.append(dict(target=target,ready_ms=round((time.perf_counter()-start)*1000,3),cache_hit=viewer.last_cache_hit))
    # Ensure at least one event-loop cycle after the synchronous cache transitions.
    until=time.perf_counter()+.15
    while time.perf_counter()<until:app.processEvents();time.sleep(.001)
    timer.stop()
    result=dict(scope='Linux Qt offscreen, four distinct generated 3840x2160 JPEGs. Ready time ends when decoded image is installed in scene; physical display presentation is not measured. Fixture generation is outside navigation timing but included in process RSS.',cold_ready_ms=round(cold_ms,2),cached_transitions=transitions,
        cache_bytes=viewer.cache.bytes_used,cache_limit_bytes=viewer.cache.max_bytes,event_loop_p95_gap_ms=round(sorted(gaps)[int(len(gaps)*.95)],2),event_loop_max_gap_ms=round(max(gaps),2),peak_process_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2))
    viewer.close()
    print(json.dumps(result,indent=2))
