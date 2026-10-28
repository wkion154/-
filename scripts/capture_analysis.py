"""Capture owned synthetic palette fixtures; no real artist/model-accuracy claims."""
import os,sys,tempfile,time,threading
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from PySide6.QtWidgets import QApplication
from app.database import Database
from app.core import Settings
from app.images import inspect_image
from app.online_service import OnlineService
from app.main_window import MainWindow
from app.theme import STYLE
from app.analysis_filters import AnalysisFilter
app=QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
def pump(predicate,seconds=8):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        app.processEvents()
        if predicate():return
        time.sleep(.005)
    raise TimeoutError('Analysis capture readiness failed')
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);db=Database(root/'library.db');settings=Settings(root/'user')
    settings.values.update(online_enabled=False,analysis_vision=False)
    service=OnlineService(db,settings,None)
    evidence={'reference':'fixture://owned-palette-study','note':'Directly generated color study, not a real policy review.'}
    service.web.repo.grant('local','local_analysis','allowed',evidence)
    for i,bg in enumerate(['#e6a8c4','#536b88','#b19bbf','#e6a8c4','#65776d','#e6a8c4']):
        path=root/f'PALETTE STUDY {i+1:02}.png';im=Image.new('RGB',(640,880),bg);draw=ImageDraw.Draw(im)
        draw.ellipse((210,100,430,320),fill='#d8c4b6');draw.rounded_rectangle((130,360,510,920),radius=120,fill='#464453')
        draw.text((30,30),f'OWNED / SYNTHETIC COLOR STUDY {i+1}',fill='white');im.save(path)
        identity=db.register(inspect_image(path))[0]
        service.web.repo.grant('local','local_analysis','allowed',evidence,'asset:'+identity)
        service.analysis.run(identity,threading.Event())
    window=MainWindow(db,settings);window.resize(1440,920);window.show();window.online_runner.timer.stop()
    pump(lambda:window.model.total==6 and len(window.gallery.thumbs)>=6)
    window.gallery.setCurrentIndex(window.model.index(0));window.toggle_analysis();dialog=window.analysis_dialog
    dialog.set_filters(AnalysisFilter(color='#e6a8c4',tolerance=12,min_share=30));dialog.apply()
    pump(lambda:window.model.total==3 and not window.model.loading and len(window.gallery.thumbs)>=3)
    
    for _ in range(30):app.processEvents();time.sleep(.01)
    window.grab().save('docs/color-gallery-v0.7.2.png')
    window.gallery.setCurrentIndex(window.model.index(0));dialog.tabs.setCurrentIndex(1)
    pump(lambda:dialog.last is not None and dialog.swatches.count()>0)
    for _ in range(30):app.processEvents();time.sleep(.01)
    dialog.grab().save('docs/analysis-ui-v0.7.2.png')
    window.close();service.analysis.close()
print('Synthetic color gallery and analysis captures saved; no external requests.')
