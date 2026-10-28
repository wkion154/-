"""Actual CPU inference on owned synthetic charts, not real-person quality evaluation."""
import json,sys,tempfile,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
import numpy as np
from app.database import Database
from app.core import Settings
from app.images import inspect_image
from app.upscale_service import UpscaleService,OPS

def chart(size):
    rng=np.random.default_rng(7);image=Image.new('RGB',size,'#594d6f');draw=ImageDraw.Draw(image)
    for _ in range(350):
        x,y=map(int,rng.integers(10,min(size)-25,2));color=tuple(map(int,rng.integers(10,245,3)));draw.rectangle((x,y,x+15,y+15),fill=color)
    draw.text((22,24),'OWNED / SYNTHETIC RESOLUTION CHART',fill='white');return image

def main():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);db=Database(root/'db');settings=Settings(root/'user');service=UpscaleService(db,settings)
        person=db.add_entity('person','SYNTHETIC STUDY · NOT A REAL PERSON');evidence={'reference':'fixture://owned-chart','note':'Owned test chart, no real-person quality evidence.'}
        high=chart((768,768));low=high.resize((384,384),Image.Resampling.LANCZOS)
        identities=[]
        for name,image in [('low',low),('high',high)]:
            path=root/(name+'.png');image.save(path);identity=db.register(inspect_image(path),person=person)[0];identities.append(identity)
            for resource in (None,'asset:'+identity):
                for op in OPS:service.sources.grant('local',op,'allowed',evidence,resource)
        records=[]
        for mode,scale in [('general',2),('general',4)]:
            start=time.monotonic();result=service.run(identities[0],threading.Event(),scale,mode)
            records.append(dict(mode=mode,scale=scale,total_seconds=round(time.monotonic()-start,3),metrics=result['metrics']))
        start=time.monotonic();result=service.run(identities[0],threading.Event(),2)
        records.append(dict(mode='general-cache-hit',scale=2,total_seconds=round(time.monotonic()-start,3),reused=result['reused']))
    print(json.dumps(dict(scope='Linux CPU, owned 384x384 synthetic chart. Reference face modes measured separately. Actual FSRCNN inference. Not artist identity/face accuracy, GPU performance, Windows or typical photo quality evidence.',results=records),indent=2))

if __name__=='__main__':main()
