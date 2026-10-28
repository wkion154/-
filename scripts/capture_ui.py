"""Generate a UI review image with clearly synthetic artwork, never artist photo data."""
import os,sys,tempfile,time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from PySide6.QtWidgets import QApplication
from app.database import Database
from app.core import Settings
from app.images import inspect_image
from app.main_window import MainWindow
from app.theme import STYLE
from app.viewer import Viewer

app=QApplication([])
app.setStyle('Fusion')
app.setStyleSheet(STYLE)
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder)
    db=Database(root/'library.db')
    settings=Settings(root/'user')
    group=db.add_entity('group','DESIGN STUDY',gender='mixed')
    person=db.add_entity('person','Sample Collection',group_id=group)
    colors=[('#6e718b','#b5a4c5'),('#98877c','#e6c7a1'),('#486568','#a8c8b2'),('#625d82','#d3bddf'),('#7a6b6e','#e2b2a4'),('#536b88','#a0bfdb'),('#8c815a','#d8cc9c'),('#69575d','#b88d96'),('#5e6c61','#a9bfa1'),('#756985','#c4b5d5'),('#727e88','#bbcbd1'),('#856f5f','#d4b398')]
    for i,(bg,fg) in enumerate(colors):
        image=Image.new('RGB',(720,960),bg)
        draw=ImageDraw.Draw(image)
        draw.ellipse((180,120,540,480),fill=fg)
        draw.rounded_rectangle((100,510,620,1050),radius=160,fill=fg)
        draw.text((40,40),'SYNTHETIC / DESIGN STUDY',fill='white')
        path=root/f'Study_{i+1:02}.png'
        image.save(path)
        db.register(inspect_image(path),person,group)
    w=MainWindow(db,settings)
    w.resize(1440,920)
    w.show()
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        app.processEvents()
        if len(w.gallery.thumbs)>=9:break
        time.sleep(.01)
    w.gallery.setCurrentIndex(w.model.index(0))
    for _ in range(10):app.processEvents()
    Path('docs').mkdir(exist_ok=True)
    w.grab().save('docs/gallery-ui.png')
    w.resize(900,1200)
    for _ in range(20):app.processEvents()
    w.grab().save('docs/portrait-ui.png')
    rows,total=db.query()
    viewer=Viewer(rows,0,total=total)
    viewer.show()
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        app.processEvents()
        if viewer.canvas.item is not None:break
        time.sleep(.01)
    viewer.grab().save('docs/viewer-ui-v0.2.png')
    viewer.close()
    w.close()
print('UI captures saved; all artwork is synthetic.')
