from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from app.main_window import MainWindow
from app.theme import STYLE
from app.images import inspect_image
from app.viewer import Viewer
from app.dialogs import EntityDialog


def window(qtbot,db,settings):
    QApplication.instance().setStyleSheet(STYLE)
    w=MainWindow(db,settings)
    qtbot.addWidget(w)
    w.show()
    return w


def test_import_gallery_thumbnail_search_favorite_and_restart(qtbot,db,settings,photo):
    w=window(qtbot,db,settings)
    qtbot.waitUntil(lambda:not w.model.loading)
    w.start_import([str(photo)])
    qtbot.waitUntil(lambda:w.import_job is None,timeout=10000)
    qtbot.waitUntil(lambda:w.model.rowCount()==1)
    qtbot.waitUntil(lambda:bool(w.gallery.thumbs),timeout=10000)
    w.gallery.setCurrentIndex(w.model.index(0))
    assert w.selected['name']==photo.name
    w.toggle_favorite()
    qtbot.waitUntil(lambda:db.query(('favorite',None))[1]==1)
    w.search.setText('missing')
    qtbot.waitUntil(lambda:w.model.total==0 and not w.model.loading)
    w.search.clear()
    qtbot.waitUntil(lambda:w.model.total==1)
    w.slider.setValue(288)
    if not w.info_panel.isVisible():w.toggle_info()
    w.toggle_info()
    w.close()
    from app.core import Settings
    restored=Settings(settings.root)
    assert restored.values['thumbnail_size']==288
    assert restored.values['info'] is False
    w2=window(qtbot,db,restored)
    w2.navigate('favorite')
    qtbot.waitUntil(lambda:w2.model.total==1)
    assert w2.info_panel.isHidden()
    w2.close()


def test_viewer_navigation_zoom_fullscreen_escape(qtbot,db,settings,photo,tmp_path):
    from PIL import Image
    db.register(inspect_image(photo))
    second=tmp_path/'second.png'
    Image.new('RGB',(640,480),'green').save(second)
    db.register(inspect_image(second))
    rows,_=db.query(sort='name')
    viewer=Viewer(rows,0)
    qtbot.addWidget(viewer)
    viewer.show()
    qtbot.waitUntil(lambda:viewer.canvas.item is not None,timeout=5000)
    original=viewer.canvas.transform().m11()
    viewer.canvas.zoom(1.2)
    assert viewer.canvas.transform().m11()>original
    with qtbot.waitSignal(viewer.image_ready,timeout=5000):
        viewer.actual()
    assert abs(viewer.canvas.transform().m11()*viewer.canvas.devicePixelRatioF()-1)<.001
    viewer.step(1)
    qtbot.waitUntil(lambda:viewer.canvas.item is not None)
    assert viewer.position==1
    viewer.fullscreen()
    assert viewer.isFullScreen()
    viewer.fullscreen()
    qtbot.keyClick(viewer,Qt.Key.Key_Escape)
    assert not viewer.isVisible()


def test_entity_dialog_and_navigation(qtbot,db,settings):
    dialog=EntityDialog(db,'group')
    qtbot.addWidget(dialog)
    dialog.name.setText('Test Group')
    dialog.kindbox.setCurrentIndex(1)
    dialog.save()
    qtbot.waitUntil(lambda:dialog.isEnabled())
    group=db.entities()['groups'][0]
    assert group['kind']=='mixed'
    p=db.add_entity('person','Member',gender='female',solo=True,group_id=group['id'])
    w=window(qtbot,db,settings)
    item=w.tree.topLevelItem(0)
    w.select_entity(item,0)
    assert w.scope==('group',group['id'])
    w.select_entity(item.child(0),0)
    assert w.scope==('person',p)
    w.navigate('solo')
    assert w.scope==('solo',None)
    w.resize(900,1000)
    qtbot.wait(50)
    assert w.gallery.width()>0
    w.close()


def test_gallery_incremental_rows_and_bounded_thumbnails(qtbot,db,settings,photo):
    meta=inspect_image(photo)
    for i in range(1000):db.register(meta|dict(sha256=f'{i:064x}',name=f'{i}.png'))
    w=window(qtbot,db,settings)
    qtbot.waitUntil(lambda:w.model.total==1000)
    assert w.model.rowCount()==1000
    assert len(w.model.rows)<=512
    assert len(w.gallery.pending)<=8
    assert len(w.gallery.thumbs)<=96
    last=w.model.index(999)
    qtbot.waitUntil(lambda:w.gallery.visualRect(last).isValid())
    w.gallery.scrollTo(last)
    qtbot.waitUntil(lambda:w.model.row_at(999,request=False) is not None)
    assert len(w.model.rows)<=w.model.MAX_PAGES*w.model.PAGE_SIZE
    w.close()


def test_viewer_fetches_beyond_gallery_page(qtbot,db,photo):
    meta=inspect_image(photo)
    for i in range(260):db.register(meta|dict(sha256=f'{i:064x}',name=f'{i:03}.png'))
    rows,total=db.query(sort='name')
    viewer=Viewer(rows,255,load_more=lambda offset:db.query(sort='name',offset=offset),total=total)
    qtbot.addWidget(viewer)
    viewer.show()
    viewer.step(1)
    qtbot.waitUntil(lambda:viewer.position==256,timeout=5000)
    assert len(viewer.rows)==260
    viewer.close()


def test_double_click_opens_viewer_and_preserves_scroll(qtbot,db,settings,photo,monkeypatch):
    from PySide6.QtCore import QTimer
    import app.main_window as module
    db.register(inspect_image(photo))
    w=window(qtbot,db,settings)
    qtbot.waitUntil(lambda:w.model.rowCount()==1)
    original=module.Viewer.exec
    opened=[]
    def execute(viewer):
        opened.append(viewer.rows[0]['id'])
        QTimer.singleShot(120,viewer.reject)
        return original(viewer)
    monkeypatch.setattr(module.Viewer,'exec',execute)
    index=w.model.index(0)
    qtbot.waitUntil(lambda:w.gallery.isVisible())
    qtbot.wait(30)
    rect=w.gallery.visualRect(index)
    qtbot.mouseClick(w.gallery.viewport(),Qt.MouseButton.LeftButton,pos=rect.center())
    qtbot.mouseDClick(w.gallery.viewport(),Qt.MouseButton.LeftButton,pos=rect.center())
    assert len(opened)==1
    assert w.gallery.currentIndex().row()==0
    w.close()
