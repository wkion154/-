"""Actual single-photo face registration test, not general identity accuracy."""
import json,sys,tempfile,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from app.database import Database
from app.core import Settings
from app.images import inspect_image
from app.upscale_service import UpscaleService,OPS
from app.super_resolution import diagnostics
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    folder=Path(directory);db=Database(folder/'db');settings=Settings(folder/'user');service=UpscaleService(db,settings)
    person=db.add_entity('person','Eileen Collins · NASA fixture')
    evidence={'reference':'https://github.com/scikit-image/scikit-image/blob/v0.24.0/skimage/data/_fetchers.py#L382-L401','note':'NASA/scikit-image public-domain declaration; software tests only, no endorsement.'}
    high=Image.open(root/'tests/fixtures/astronaut.png').convert('RGB');low=high.resize((256,256),Image.Resampling.LANCZOS);ids=[]
    for name,image in [('low',low),('reference',high)]:
        path=folder/(name+'.png');image.save(path);identity=db.register(inspect_image(path),person=person)[0];ids.append(identity)
        for resource in (None,'asset:'+identity):
            for op in OPS:service.sources.grant('local',op,'allowed',evidence,resource)
    results=[]
    for mode,scale in [('general',2),('general',4),('reference',2),('reference',4)]:
        start=time.monotonic();result=service.run(ids[0],threading.Event(),scale,mode)
        results.append(dict(mode=mode,scale=scale,total_seconds=round(time.monotonic()-start,3),metrics=result['metrics']))
    start=time.monotonic();result=service.run(ids[0],threading.Event(),2,'reference')
    results.append(dict(mode='reference-cache-hit',scale=2,total_seconds=round(time.monotonic()-start,3),reused=result['reused']))
print(json.dumps(dict(scope='NASA/scikit-image public-domain photo: 256px downsample and 512px source reference, actual face-mesh/FSRCNN execution. A same-photo registration test, not varied-pose/identity-preservation or K-pop accuracy evidence.',diagnostics=diagnostics(),results=results),indent=2))
