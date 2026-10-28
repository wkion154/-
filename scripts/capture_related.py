"""Create reproducible UI evidence with owned synthetic artwork only."""
import os,sys,tempfile,time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from PySide6.QtWidgets import QApplication
from app.database import Database
from app.core import Settings
from app.images import inspect_image
from app.related import RelatedRepository
from app.related_dialog import RelatedDialog
from app.viewer import Viewer
from app.theme import STYLE


def pump(predicate,seconds=8):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        app.processEvents()
        if predicate():return
        time.sleep(.005)
    raise TimeoutError('UI capture did not become ready')

app=QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);db=Database(root/'library.db');settings=Settings(root/'user');settings.values['online_enabled']=False
    person=db.add_entity('person','DESIGN STUDY · 가상 인물',gender='female')
    group=db.add_entity('group','OWNED SYNTHETIC COLLECTION')
    colors=[('#515a73','#b5a4c5'),('#6b647a','#dbb7c7'),('#4e6566','#b5cec3'),('#6b5f65','#e6c7a1'),('#536b88','#a0bfdb'),('#74697e','#d3bddf'),('#5e6c61','#a9bfa1'),('#727e88','#bbcbd1')]
    ids=[]
    for i,(bg,fg) in enumerate(colors):
        image=Image.new('RGB',(640,880),bg);draw=ImageDraw.Draw(image)
        dx=(i%3-1)*25;draw.ellipse((210+dx,90,430+dx,310),fill=fg)
        draw.rounded_rectangle((100+dx,350,540+dx,920),radius=150,fill=fg)
        draw.text((30,30),'OWNED / SYNTHETIC STUDY',fill='white')
        path=root/f'Studio_{i+1:02}.png';image.save(path)
        identity=db.register(inspect_image(path),person,group)[0];ids.append(identity)
    with db.connect() as c:c.execute('UPDATE assets SET captured=?',('2026-10-01',))
    repo=RelatedRepository(db);repo.override(ids[0],ids[1],'confirmed_event','직접 생성한 디자인 샘플 · 동일 제작 세션 확인')
    dialog=RelatedDialog(db,settings,ids[0]);dialog.show()
    pump(lambda:dialog.model.rowCount()==7 and len(dialog.gallery.thumbs)>=7)
    dialog.grab().save('docs/related-ui-v0.5.png')
    viewer=Viewer([repo.asset(ids[0])],0,related_repo=repo,related_cache=settings.cache)
    viewer.show();pump(lambda:viewer.canvas.item is not None)
    viewer.toggle_related_strip();pump(lambda:viewer.related_strip.loaded==7)
    viewer.grab().save('docs/related-viewer-v0.5.png')
    viewer.close()
    evidence={'reference':'fixture://owned-design-study','note':'Directly generated artwork; no artist photos or external site rights asserted.'}
    for op in ('local_analysis','ai_restore_input'):
        repo.sources.grant('local',op,'allowed',evidence)
        for identity in ids[:3]:repo.sources.grant('local',op,'allowed',evidence,'asset:'+identity)
    dialog.mode.setCurrentIndex(1)
    pump(lambda:dialog.model.rowCount()==2 and len(dialog.gallery.thumbs)==2)
    dialog.grab().save('docs/reference-candidates-ui-v0.5.png')
    dialog.close()
print('Synthetic related gallery and viewer captures saved.')
