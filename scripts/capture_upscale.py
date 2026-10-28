"""Actual FSRCNN and face-only reference on NASA/scikit-image public-domain fixture."""
import os,sys,tempfile,time,threading
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from PIL import Image
from app.database import Database
from app.core import Settings
from app.images import inspect_image
from app.upscale_service import UpscaleService,OPS
from app.upscale_dialog import UpscaleDialog
from app.theme import STYLE
from profile_upscale import chart
app=QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
def pump(predicate,seconds=12):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        app.processEvents()
        if predicate():return
        time.sleep(.005)
    raise TimeoutError('Restoration capture readiness failed')
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);db=Database(root/'db');settings=Settings(root/'user');service=UpscaleService(db,settings)
    person=db.add_entity('person','Eileen Collins · NASA 공개 표본');evidence={'reference':'https://github.com/scikit-image/scikit-image/blob/v0.24.0/skimage/data/_fetchers.py#L382-L401','note':'NASA photograph; public-domain declaration in official scikit-image data docs. Local software test, no endorsement or K-pop accuracy claim.'}
    high=Image.open(Path(__file__).resolve().parents[1]/'tests/fixtures/astronaut.png').convert('RGB');low=high.resize((256,256),Image.Resampling.LANCZOS);identities=[]
    for name,image in [('NASA scikit-image · LOW',low),('NASA scikit-image · REFERENCE',high)]:
        path=root/(name+'.png');image.save(path);identity=db.register(inspect_image(path),person=person)[0];identities.append(identity)
        for resource in (None,'asset:'+identity):
            for op in OPS:service.sources.grant('local',op,'allowed',evidence,resource)
    dialog=UpscaleDialog(service,identities[0]);dialog.show();pump(lambda:dialog.ready)
    dialog.run('general');pump(lambda:not dialog.busy);assert 'general' in dialog.results
    dialog.run('reference');pump(lambda:not dialog.busy);assert 'reference' in dialog.results
    dialog.canvas.zoom=6.5;dialog.canvas.center.setX(.44);dialog.canvas.center.setY(.25)
    for _ in range(20):app.processEvents();time.sleep(.01)
    dialog.grab().save('docs/upscale-compare-v0.7.3.png')
    dialog.compare_mode.setCurrentIndex(2);dialog.canvas.zoom=6.;dialog.set_split(.5)
    for _ in range(20):app.processEvents();time.sleep(.01)
    dialog.grab().save('docs/upscale-slider-v0.7.3.png');dialog.close()
print('Actual FSRCNN / face-only assistance on NASA public-domain fixture captured; see fixture provenance.')
