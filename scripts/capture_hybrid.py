"""Owned local/cached/link-only fixtures; no external site or real artist photos."""
import os,sys,tempfile,time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from PySide6.QtWidgets import QApplication
from app.database import Database
from app.core import Settings
from app.images import inspect_image
from app.source_repository import SourceRepository
from app.page_discovery import discover_html
from app.library import LibraryRepository
from app.main_window import MainWindow
from app.viewer import Viewer
from app.theme import STYLE

app=QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
def pump(predicate,seconds=8):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        app.processEvents()
        if predicate():return
        time.sleep(.005)
    raise TimeoutError('Synthetic hybrid capture readiness failed')

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);db=Database(root/'library.db');settings=Settings(root/'user');settings.values['online_enabled']=False
    source=SourceRepository(db);source.save_spec(dict(id='owned-design',name='직접 생성한 샘플 · 실사이트 아님',category='user',adapter='html',domains=['owned-design.example']))
    source.enable('owned-design',True)
    evidence={'reference':'fixture://owned-design-study','note':'Directly generated artwork. Not a real source policy review.'}
    for op in ('metadata_search','thumbnail','original_download'):source.grant('owned-design',op,'allowed',evidence)
    page=source.add_page('owned-design','https://owned-design.example/study')
    html=''.join(f'<img src="https://owned-design.example/{i}.png" width="640" height="880" alt="ONLINE STUDY {i:02}">' for i in range(3,6))
    source.index_page(source.page(page),discover_html(html,'https://owned-design.example/study'))
    remote_rows=source.images(page_id=page)
    colors=[('#515a73','#b5a4c5'),('#6b647a','#dbb7c7'),('#4e6566','#b5cec3'),('#6b5f65','#e6c7a1'),('#536b88','#a0bfdb')]
    for i,(bg,fg) in enumerate(colors):
        path=root/f'LOCAL STUDY {i:02}.png';image=Image.new('RGB',(640,880),bg);draw=ImageDraw.Draw(image)
        draw.ellipse((210,90,430,310),fill=fg);draw.rounded_rectangle((100,350,540,920),radius=150,fill=fg)
        draw.text((30,30),'OWNED / SYNTHETIC STUDY',fill='white');image.save(path)
        if i<3:db.register(inspect_image(path))
        else:
            item=remote_rows[i-3];identity='web:'+item['id']
            for op in ('thumbnail','original_download'):source.grant('owned-design',op,'allowed',evidence,identity)
            source.measured(item['id'],inspect_image(path),path)
    person=db.add_entity('person','DESIGN STUDY · 가상 인물',gender='female')
    repo=LibraryRepository(db);chosen='web:'+remote_rows[0]['id'];repo.associate(chosen,[person],[]);repo.favorite(chosen,True)
    window=MainWindow(db,settings);window.resize(1440,920);window.show();window.online_runner.timer.stop()
    pump(lambda:window.model.total==6 and len(window.gallery.thumbs)>=5)
    position=next(i for i,row in enumerate(window.model.rows) if row['id']==chosen)
    window.gallery.setCurrentIndex(window.model.index(position));pump(lambda:'온라인 후보' in window.info_text.text())
    for _ in range(12):app.processEvents()
    window.grab().save('docs/hybrid-gallery-v0.7.1.png')
    rows,total=repo.query()
    position=next(i for i,row in enumerate(rows) if row['id']==chosen)
    viewer=Viewer(rows,position,row_resolver=window.library_service.original)
    viewer.show();pump(lambda:viewer.displayed_id==chosen)
    for _ in range(12):app.processEvents()
    viewer.grab().save('docs/hybrid-viewer-v0.7.1.png')
    viewer.close();window.close()
print('Owned synthetic hybrid gallery/viewer captures saved; no external requests.')
