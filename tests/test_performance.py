"""Behavioral regression checks for bounded memory/work and large-result navigation."""
import threading
import time
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from app.cache import ImageCache
from app.gallery import GalleryModel
from app.images import decode_preview
from app.viewer import Viewer,Canvas


def fill_records(db,count):
    with db.connect() as conn:
        conn.executemany('INSERT INTO assets(id,path,name,sha256,width,height,size,format,registered) VALUES(?,?,?,?,?,?,?,?,?)',
            [(str(i),'not-a-real-file',f'photo-{i:05}.jpg',f'{i:064x}',800,1200,10000,'JPEG',i) for i in range(count)])


def test_metadata_page_cache_bounded_across_10000_results(qtbot,db):
    fill_records(db,10000)
    model=GalleryModel(db)
    model.reload(sort='name')
    qtbot.waitUntil(lambda:model.total==10000)
    assert model.rowCount()==10000
    assert len(model.rows)==256
    for number in range(0,10000,769):
        model.row_at(number)
        qtbot.waitUntil(lambda:model.row_at(number,request=False) is not None)
        assert len(model.rows)<=2048
        assert len(model.pending_pages)<=2
    assert model.row_at(0,request=False) is None
    model.row_at(9999)
    qtbot.waitUntil(lambda:model.row_at(9999,request=False) is not None)
    assert model.row_at(9999)['name']=='photo-09999.jpg'
    assert len(model.pages)<=8
    model.close_tasks()


def test_search_reload_ignores_old_worker_result(qtbot,db,monkeypatch):
    fill_records(db,300)
    original=db.query
    started=threading.Event()
    def slow(*args,**kwargs):
        if kwargs.get('search',args[1] if len(args)>1 else '')=='photo-000':
            started.set()
            time.sleep(.08)
        return original(*args,**kwargs)
    monkeypatch.setattr(db,'query',slow)
    model=GalleryModel(db)
    model.reload(search='photo-000')
    qtbot.waitUntil(started.is_set)
    model.reload(search='does not exist')
    qtbot.waitUntil(lambda:not model.loading)
    assert model.total==0 and model.rows==[]
    model.close_tasks()


def test_decoded_cache_enforces_bytes_and_small_image_count():
    cache=ImageCache(max_bytes=800,max_items=2)
    image=QImage(10,10,QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    assert cache.put('a',image)
    assert cache.put('b',image)
    assert cache.get('a') is image
    cache.put('c',image)
    assert 'b' not in cache.items and cache.bytes_used==800
    assert not cache.put('large',QImage(20,20,QImage.Format.Format_RGB32))
    cache.put('tiny',QImage(1,1,QImage.Format.Format_RGB32))
    assert len(cache.items)==2 and cache.bytes_used<=800
    cache.clear()
    assert cache.bytes_used==0


@pytest.mark.parametrize('suffix',['png','jpg','webp','bmp','tiff'])
def test_native_decoder_formats_and_exif(tmp_path,suffix):
    path=tmp_path/f'picture.{suffix}'
    original=Image.new('RGB',(1600,800),'#3278c8')
    original.save(path)
    result=decode_preview(path,400)
    assert (result.width(),result.height())==(400,200)
    assert not result.isNull()


def test_native_decoder_rotates_and_rejects_missing(tmp_path):
    path=tmp_path/'rotated.jpg'
    exif=Image.Exif();exif[274]=6
    Image.new('RGB',(1600,800),'red').save(path,exif=exif)
    image=decode_preview(path,400)
    assert (image.width(),image.height())==(200,400)
    with pytest.raises(ValueError):decode_preview(tmp_path/'missing.png')


def test_prefetch_reuses_neighbor_without_another_decode(qtbot,monkeypatch):
    import app.viewer as module
    calls=[]
    def decode(path,edge=4096):
        calls.append(path)
        image=QImage(80,120,QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        return image
    monkeypatch.setattr(module,'decode_preview',decode)
    rows=[dict(id=str(i),sha256=str(i),path=str(i),name=f'{i}.png',width=80,height=120,format='PNG',favorite=False) for i in range(3)]
    viewer=Viewer(rows,1)
    qtbot.addWidget(viewer);viewer.show()
    qtbot.waitUntil(lambda:viewer.canvas.item is not None)
    qtbot.waitUntil(lambda:len(viewer.cache.items)==3)
    before=len(calls)
    viewer.step(1)
    assert viewer.last_cache_hit
    assert len(calls)==before
    assert viewer.displayed_id=='2'
    assert viewer.cache.bytes_used<=viewer.cache.max_bytes
    viewer.close()
    assert not viewer.prefetch_timer.isActive()
    assert viewer.cache.bytes_used==0


def test_viewer_can_page_backward_from_arbitrary_gallery_position(qtbot,db,monkeypatch):
    import app.viewer as module
    fill_records(db,2000)
    image=QImage(8,12,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.red)
    monkeypatch.setattr(module,'decode_preview',lambda *args:image)
    rows,total=db.query(sort='name',offset=1024)
    viewer=Viewer(rows,0,load_more=lambda offset:db.query(sort='name',offset=offset),total=total,base_index=1024)
    qtbot.addWidget(viewer);viewer.show()
    viewer.step(-1)
    qtbot.waitUntil(lambda:viewer.base_index+viewer.position==1023)
    assert viewer.rows[viewer.position]['name']=='photo-01023.jpg'
    assert len(viewer.rows)<=viewer.MAX_RECORDS
    # Moving across several forward pages must not retain the complete result set.
    for target in (1280,1536,1792):
        viewer.position=len(viewer.rows)-1
        viewer.step(1)
        qtbot.waitUntil(lambda:not viewer.fetching)
        assert len(viewer.rows)<=viewer.MAX_RECORDS
    viewer.close()


def test_width_height_fit_and_source_pixel_zoom(qtbot):
    canvas=Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(800,600);canvas.show()
    image=QImage(400,200,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.blue)
    canvas.show_image(image,source_width=1600)
    canvas.fit('width')
    assert abs(canvas.transform().m11()*400-(canvas.viewport().width()-4))<1
    canvas.fit('height')
    assert abs(canvas.transform().m11()*200-(canvas.viewport().height()-4))<1
    canvas.actual()
    assert abs(canvas.physical_zoom()-1)<.00001


def test_fullscreen_chrome_hides_and_returns_on_activity(qtbot,db,photo):
    from app.images import inspect_image
    db.register(inspect_image(photo))
    rows,total=db.query()
    viewer=Viewer(rows,0)
    qtbot.addWidget(viewer);viewer.show()
    qtbot.waitUntil(lambda:viewer.canvas.item is not None)
    viewer.fullscreen();viewer.hide_chrome()
    assert viewer.controls.isHidden()
    viewer.canvas.activity.emit()
    assert not viewer.controls.isHidden()
    viewer.close()


def test_viewport_image_paint_preserves_color_without_pixmap_copy(qtbot):
    canvas=Canvas()
    qtbot.addWidget(canvas)
    canvas.resize(600,400);canvas.show()
    image=QImage(800,600,QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    canvas.show_image(image)
    qtbot.wait(30)
    result=canvas.viewport().grab().toImage()
    pixel=result.pixelColor(result.width()//2,result.height()//2)
    assert pixel.red()>240 and pixel.green()<10
    assert canvas.item.image.cacheKey()==image.cacheKey()


def test_stable_id_position_and_refresh_scroll_anchor(qtbot,db,settings,photo):
    from app.main_window import MainWindow
    from app.images import inspect_image
    meta=inspect_image(photo)
    with db.connect() as conn:
        conn.executemany('INSERT INTO assets(id,path,name,sha256,width,height,size,format,registered) VALUES(?,?,?,?,?,?,?,?,?)',
            [(str(i),str(photo),f'photo-{i:04}.png',f'{i:064x}',800,1200,10000,'PNG',i) for i in range(400)])
    assert db.locate('100',sort='oldest')==100
    assert db.locate('100',search='unmatched') is None
    w=MainWindow(db,settings)
    qtbot.addWidget(w);w.show()
    qtbot.waitUntil(lambda:w.model.total==400)
    target=w.model.index(100)
    qtbot.waitUntil(lambda:w.gallery.visualRect(target).isValid())
    w.gallery.scrollTo(target)
    qtbot.wait(50)
    from PySide6.QtCore import QPoint
    anchor=w.gallery.indexAt(QPoint(12,12))
    assert anchor.isValid()
    before=w.model.row_at(anchor.row())['id']
    w.gallery.setCurrentIndex(anchor)
    before_y=w.gallery.visualRect(anchor).top()
    # Insert a newer image ahead of the viewport in the default newest order.
    db.register(meta|dict(registered=10000,sha256='f'*64))
    w.reload(preserve=True)
    qtbot.waitUntil(lambda:w.model.total==401)
    qtbot.waitUntil(lambda:w.selected is not None and w.selected['id']==before)
    qtbot.waitUntil(lambda:w.gallery.anchor is None)
    restored=w.model.index(db.locate(before))
    # Inserting one item changes grid columns; preserve the photo's vertical anchor,
    # not an impossible identical top-left column after the grid reflows.
    assert abs(w.gallery.visualRect(restored).top()-before_y)<=1
    assert w.gallery.visualRect(restored).intersects(w.gallery.viewport().rect())
    w.close()


def test_fast_navigation_cancels_queued_decode_and_drops_stale_image(qtbot,monkeypatch):
    import app.viewer as module
    calls=[]
    started=threading.Event()
    def decode(path,edge=4096):
        calls.append(path)
        if path=='0':
            started.set();time.sleep(.07)
        image=QImage(80,120,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.red)
        return image
    monkeypatch.setattr(module,'decode_preview',decode)
    monkeypatch.setattr(Viewer,'prefetch_neighbors',lambda self:None)
    rows=[dict(id=str(i),sha256=str(i),path=str(i),name=str(i),width=80,height=120,format='PNG',favorite=False) for i in range(3)]
    viewer=Viewer(rows,0)
    qtbot.addWidget(viewer);viewer.show()
    qtbot.waitUntil(started.is_set)
    viewer.step(1);viewer.step(1)
    qtbot.waitUntil(lambda:viewer.displayed_id=='2')
    assert calls==['0','2']
    assert viewer.rows[viewer.position]['id']=='2'
    viewer.close()


def test_failed_metadata_page_waits_for_explicit_refresh(qtbot,db,monkeypatch):
    fill_records(db,600)
    original=db.query
    attempts=[]
    fail=[True]
    def query(*args,**kwargs):
        offset=args[3] if len(args)>3 else kwargs.get('offset',0)
        if offset:
            attempts.append(offset)
            if fail[0]:raise OSError('Simulated unavailable database')
        return original(*args,**kwargs)
    monkeypatch.setattr(db,'query',query)
    model=GalleryModel(db)
    errors=[];model.failed.connect(errors.append)
    model.reload();qtbot.waitUntil(lambda:not model.loading)
    model.row_at(300)
    qtbot.waitUntil(lambda:len(errors)==1)
    for _ in range(20):model.row_at(300)
    qtbot.wait(30)
    assert len(attempts)==1
    fail[0]=False
    model.reload();qtbot.waitUntil(lambda:not model.loading)
    model.row_at(300)
    qtbot.waitUntil(lambda:model.row_at(300,request=False) is not None)
    assert len(attempts)==2
    model.close_tasks()
