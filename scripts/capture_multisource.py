"""Owned synthetic fixtures only; no external site collection is represented."""
import os,sys,tempfile,time,json
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from PySide6.QtWidgets import QApplication
from app.core import Settings
from app.database import Database
from app.main_window import MainWindow
from app.online_dialog import OnlineDialog
from app.page_discovery import discover_html
from app.images import inspect_image
from app.theme import STYLE

app=QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);settings=Settings(root/'user');settings.values['online_enabled']=False
    window=MainWindow(Database(root/'library.sqlite3'),settings)
    repo=window.online_service.web.repo
    spec=dict(id='owned-ui',name='합성 이미지 검증 출처 · 실사이트 아님',category='official',adapter='html',domains=['fixture.example'],original_attributes=['data-original'])
    repo.save_spec(spec)
    evidence={'reference':'fixture://owned-design-study','note':'Owned generated artwork. Not a live website policy review.','attribution':'Synthetic UI study'}
    permissions=dict(metadata_search='allowed',thumbnail='allowed',original_download='allowed',local_analysis='denied',ai_restore_input='denied',result_storage='denied',browser_render='review')
    repo.grant_many('owned-ui',permissions,evidence,enabled=True)
    page=repo.add_page('owned-ui','https://fixture.example/owned-study')
    markup='<title>합성 자료로 확인한 UI · 실사이트 수집 아님</title><meta property="article:published_time" content="2026-10-28T10:00:00+09:00"><picture><img alt="직접 생성한 디자인 샘플" src="/study-320.png" srcset="/study-800.png 800w, /study-1600.png 1600w" data-original="/study-1600.png"></picture>'
    repo.index_page(repo.page(page),discover_html(markup,'https://fixture.example/owned-study',('data-original',)))
    artwork=Image.new('RGB',(1600,2400),'#646781');draw=ImageDraw.Draw(artwork)
    draw.ellipse((440,300,1160,1020),fill='#cab6c6');draw.rounded_rectangle((220,1080,1380,2600),radius=300,fill='#cab6c6')
    draw.text((80,80),'SYNTHETIC / OWNED DESIGN STUDY',fill='white')
    for item in repo.images(page_id=page):
        width=int(Path(item['url']).stem.rsplit('-',1)[-1]);height=width*3//2
        path=root/f'study-{width}.png';artwork.resize((width,height)).save(path)
        repo.grant_many('owned-ui',permissions,evidence,'web:'+item['id'])
        repo.measured(item['id'],inspect_image(path),path)
    dialog=OnlineDialog(window.online_service,window.online_runner,settings,window)
    dialog.resize(1380,940);dialog.show();dialog.tabs.setCurrentWidget(dialog.source_pane)
    pane=dialog.source_pane
    def pump(condition,seconds=5):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            app.processEvents()
            if condition():return
            time.sleep(.005)
        raise TimeoutError('Capture readiness failed')
    pump(lambda:not pane.loading)
    pane.sources.setCurrentIndex(pane.sources.findData('owned-ui'))
    pump(lambda:not pane.loading and len(pane.rows)==3)
    index=next(i for i,x in enumerate(pane.rows) if x['width']==1600)
    pane.table.selectRow(index)
    pump(lambda:pane.preview.pixmap() is not None and not pane.preview.pixmap().isNull())
    until=time.monotonic()+.08
    while time.monotonic()<until:app.processEvents();time.sleep(.005)
    dialog.grab().save('docs/multisource-ui-v0.4.png')
    dialog.close();window.close()
print('Captured owned synthetic source UI; no network requests or real artist photos.')
