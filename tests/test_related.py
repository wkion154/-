import json
import sqlite3
import threading
from pathlib import Path
import pytest
from app.database import Database,SCHEMA
from app.images import inspect_image
from app.related import RelatedRepository,calendar_day
from app.providers.http import Cancelled
from app.source_repository import SourceRepository
from test_sources import web, indexed, permit, image_bytes

EVIDENCE={'reference':'fixture://owned','note':'Owned generated image, tested rights only.'}


def add(db,tmp_path,name,person=None,group=None,color='purple',size=(80,120)):
    from PIL import Image
    path=tmp_path/(name+'.png');Image.new('RGB',size,color).save(path)
    return db.register(inspect_image(path),person,group)[0]


def test_explained_relationships_dates_not_registration_and_manual_overrides(db,tmp_path):
    person=db.add_entity('person','Confirmed A')
    group=db.add_entity('group','Collection')
    seed=add(db,tmp_path,'seed',person,group)
    same=add(db,tmp_path,'same',person,color='pink')
    unrelated=add(db,tmp_path,'other',color='blue')
    date_only=add(db,tmp_path,'dated',color='red')
    with db.connect() as c:
        c.execute('UPDATE assets SET captured=? WHERE id IN (?,?)',('2024-05-02',seed,date_only))
    repo=RelatedRepository(db);result=repo.search(seed,threading.Event())
    assert {r['id'] for r in result['rows']}=={same,date_only}
    assert all(r['relation']=='metadata' for r in result['rows'])
    assert all('사용자 확인' not in ' '.join(r['reasons']) for r in result['rows'])
    repo.override(seed,unrelated,'confirmed_event','User checked event record fixture')
    repo.override(seed,same,'excluded','Unrelated session')
    rows=repo.search(seed,threading.Event())['rows']
    assert rows[0]['id']==unrelated and rows[0]['relation']=='confirmed_event'
    assert same not in {r['id'] for r in rows}
    # Exclusions and manual links are symmetric; nothing is deleted or merged.
    assert seed not in {r['id'] for r in repo.search(same,threading.Event())['rows']}
    repo.clear_override(same,seed)
    assert seed in {r['id'] for r in repo.search(same,threading.Event())['rows']}
    with db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM assets').fetchone()[0]==4
        audit=json.loads(c.execute('SELECT payload FROM related_searches WHERE seed_id=?',(seed,)).fetchone()[0])
    assert audit['candidates'][0]['reasons']


def test_unknown_people_and_posted_dates_do_not_assert_same_shoot(db,tmp_path):
    person=db.add_entity('person','Mention only')
    seed=add(db,tmp_path,'a',person)
    other=add(db,tmp_path,'b',person,color='green')
    with db.connect() as c:
        c.execute("UPDATE image_person SET status='suggested' WHERE image_id=?",(other,))
    repo=RelatedRepository(db)
    assert not repo.search(seed,threading.Event())['rows']
    with db.connect() as c:
        c.execute('UPDATE assets SET published=?',('2025-06-07T12:00:00Z',))
    rows=repo.search(seed,threading.Event())['rows']
    assert len(rows)==1 and rows[0]['score']==3
    assert rows[0]['relation']=='metadata'
    assert '게시' in rows[0]['reasons'][0]
    assert calendar_day('2025-06') is None
    assert calendar_day('2025-02-30') is None
    assert calendar_day('2025-06-07nonsense') is None


def test_reference_requires_both_input_rights_and_explicit_person(db,tmp_path):
    p=db.add_entity('person','A');q=db.add_entity('person','B')
    seed=add(db,tmp_path,'seed',p);other=add(db,tmp_path,'b',p,color='yellow',size=(160,240))
    wrong=add(db,tmp_path,'wrong',q,color='blue')
    repo=RelatedRepository(db)
    assert '권한' in repo.search(seed,threading.Event(),True)['message']
    for asset in (seed,other,wrong):
        for op in ('local_analysis','ai_restore_input'):
            repo.sources.grant('local',op,'allowed',EVIDENCE)
            repo.sources.grant('local',op,'allowed',EVIDENCE,'asset:'+asset)
    rows=repo.search(seed,threading.Event(),True)['rows']
    assert [r['id'] for r in rows]==[other]
    assert '미평가' in rows[0]['reasons'][-2]
    repo.sources.grant('local','ai_restore_input','denied',EVIDENCE,'asset:'+other)
    assert not repo.search(seed,threading.Event(),True)['rows']
    db.register(inspect_image(tmp_path/'seed.png'),q)
    assert '선택' in repo.search(seed,threading.Event(),True)['message']
    with pytest.raises(ValueError):repo.search(seed,threading.Event(),True,'unknown-person')


def test_web_origin_cannot_inherit_local_rights_and_revisions_require_review(web,tmp_path):
    service,client,resources=web;db=service.db
    person=db.add_entity('person','A')
    seed=add(db,tmp_path,'seed',person)
    _,image=indexed(web)
    resources[image['url']]=(image_bytes(),'image/png')
    permit(service.repo,'original_download');permit(service.repo,'original_download','web:'+image['id'])
    service.probe(image['id'],threading.Event())
    service.download(image['id'],threading.Event(),[person])
    item=service.repo.image(image['id']);asset=item['local_asset_id']
    # Even an extra local marker cannot bypass the actual web source.
    with db.connect() as c:c.execute("INSERT INTO asset_sources(id,asset_id,source_id) VALUES('extra-local',?,'local')",(asset,))
    repo=RelatedRepository(db)
    assert not repo.is_local(asset)
    assert not repo.allowed(asset,('thumbnail',))
    permit(service.repo,'thumbnail');permit(service.repo,'thumbnail','web:'+image['id'])
    assert repo.allowed(asset,('thumbnail',))
    assert asset in {r['id'] for r in repo.search(seed,threading.Event())['rows']}
    resources[image['url']]=(image_bytes(color='orange'),'image/png')
    service.probe(image['id'],threading.Event())
    assert not repo.allowed(asset,('thumbnail',))


def test_bounded_cancelled_search_and_validation(db,tmp_path,monkeypatch):
    person=db.add_entity('person','A');seed=add(db,tmp_path,'seed',person)
    for i in range(6):add(db,tmp_path,str(i),person,color=(20*i,70,80))
    repo=RelatedRepository(db);monkeypatch.setattr(repo,'PER_REASON',3);monkeypatch.setattr(repo,'MAX_RESULTS',2)
    result=repo.search(seed,threading.Event())
    assert result['truncated'] and len(result['rows'])==2
    cancelled=threading.Event();cancelled.set()
    with pytest.raises(Cancelled):repo.search(seed,cancelled)
    with pytest.raises(ValueError):repo.override(seed,seed,'related','reason')
    with pytest.raises(ValueError):repo.override(seed,'not-found','related','')


def test_v3_migration_backup_and_transactional_failure(tmp_path,monkeypatch):
    from app.online_schema import SCHEMA_V2
    from app.source_schema import SCHEMA_V3
    import app.related_schema as schema
    path=tmp_path/'v3.db'
    with sqlite3.connect(path) as c:
        c.executescript(SCHEMA);c.executescript(SCHEMA_V2);c.executescript(SCHEMA_V3)
        c.execute("INSERT INTO groups VALUES('kept','Original','mixed','')")
        c.execute('PRAGMA user_version=3')
    original=schema.SCHEMA_V4;monkeypatch.setattr(schema,'SCHEMA_V4',original+'; NOT SQL;')
    with pytest.raises(sqlite3.Error):Database(path)
    with sqlite3.connect(path) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==3
        assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='related_overrides'").fetchone()[0]==0
    backups=list(tmp_path.glob('*.v3-*.bak'));assert len(backups)==1
    with sqlite3.connect(backups[0]) as c:assert c.execute('PRAGMA user_version').fetchone()[0]==3
    monkeypatch.setattr(schema,'SCHEMA_V4',original);db=Database(path)
    assert db.entities()['groups'][0]['name']=='Original'
    with db.connect() as c:assert c.execute('PRAGMA user_version').fetchone()[0]==4


def test_related_ui_async_results_viewer_signal_and_shutdown(qtbot,db,settings,tmp_path):
    from app.related_dialog import RelatedDialog
    from app.viewer import Viewer
    p=db.add_entity('person','A')
    seed=add(db,tmp_path,'seed',p);other=add(db,tmp_path,'other',p,color='pink')
    dialog=RelatedDialog(db,settings,seed);qtbot.addWidget(dialog);dialog.show()
    qtbot.waitUntil(lambda:dialog.model.rowCount()==1,timeout=5000)
    assert dialog.selected()['id']==other
    qtbot.waitUntil(lambda:bool(dialog.gallery.thumbs),timeout=5000)
    dialog.mode.setCurrentIndex(1)
    qtbot.waitUntil(lambda:'권한' in dialog.status.text(),timeout=5000)
    assert dialog.model.rowCount()==0
    dialog.close();assert dialog.closed and dialog.tasks.stopped and dialog.gallery.tasks.stopped
    viewer=Viewer([RelatedRepository(db).asset(seed)],0);qtbot.addWidget(viewer)
    received=[];viewer.related_requested.connect(received.append)
    viewer.related();assert received==[seed]
    viewer.close()


def test_visual_candidates_use_current_features_and_revoked_analysis_is_excluded(web,tmp_path):
    from app.page_discovery import discover_html
    service,client,resources=web;db=service.db
    page,first=indexed(web)
    page2=service.repo.add_page('owned','https://photos.example/second')
    service.repo.index_page(service.repo.page(page2),discover_html('<img src="https://cdn.example/b.png">','https://photos.example/second'))
    second=next(r for r in service.repo.images() if r['id']!=first['id'])
    assets=[]
    for i,item in enumerate((first,second)):
        import io
        from PIL import Image
        encoded=io.BytesIO()
        with Image.open(io.BytesIO(image_bytes())) as pixels:pixels.save(encoded,format='PNG',compress_level=(1,9)[i])
        resources[item['url']]=(encoded.getvalue(),'image/png')
        for op in ('original_download','thumbnail','local_analysis'):
            permit(service.repo,op);permit(service.repo,op,'web:'+item['id'])
        service.probe(item['id'],threading.Event());service.download(item['id'],threading.Event())
        service.analyze(item['id'],threading.Event())
        assets.append(service.repo.image(item['id'])['local_asset_id'])
    repo=RelatedRepository(db);rows=repo.search(assets[0],threading.Event())['rows']
    assert len(rows)==1 and rows[0]['relation']=='visual_candidate'
    assert '시각적 유사' in rows[0]['reasons'][0]
    # Stored relations are only candidates: recompute distance from current hashes.
    with db.connect() as c:c.execute('UPDATE web_images SET dhash=? WHERE id=?',('ffffffffffffffff',second['id']))
    assert not repo.search(assets[0],threading.Event())['rows']
    with db.connect() as c:
        current=c.execute('SELECT dhash FROM web_images WHERE id=?',(first['id'],)).fetchone()[0]
        c.execute('UPDATE web_images SET dhash=? WHERE id=?',(current,second['id']))
    service.repo.grant('owned','local_analysis','denied',EVIDENCE,'web:'+second['id'])
    assert not repo.search(assets[0],threading.Event())['rows']


def test_filmstrip_is_opt_in_bounded_and_rejects_late_result(qtbot,db,settings,tmp_path):
    from app.viewer import Viewer
    person=db.add_entity('person','A')
    seed=add(db,tmp_path,'seed',person)
    for i in range(15):add(db,tmp_path,f'related-{i}',person,color=(i*10,150,90))
    repo=RelatedRepository(db)
    viewer=Viewer([repo.asset(seed)],0,related_repo=repo,related_cache=settings.cache)
    qtbot.addWidget(viewer);viewer.show()
    strip=viewer.related_strip
    assert strip.isHidden() and strip.list.count()==0 and not strip.tasks.jobs
    viewer.toggle_related_strip()
    qtbot.waitUntil(lambda:strip.loaded==12,timeout=5000)
    requested=[];viewer.related_requested.connect(requested.append)
    strip.activate(strip.list.item(0));assert len(requested)==1
    viewer.fullscreen();viewer.hide_chrome()
    assert strip.isHidden() and viewer.strip_auto_hidden
    viewer.show_chrome();assert not strip.isHidden() and not viewer.strip_auto_hidden
    viewer.fullscreen()
    viewer.toggle_related_strip();assert strip.isHidden() and strip.list.count()==0
    viewer.toggle_related_strip();viewer.close()
    assert strip.closed and strip.tasks.stopped and strip.list.count()==0


def test_manual_relationship_save_finishes_before_dialog_closes(qtbot,db,settings,tmp_path):
    from app.related_dialog import RelatedDialog
    import time
    p=db.add_entity('person','A');seed=add(db,tmp_path,'seed',p);other=add(db,tmp_path,'other',p,color='green')
    dialog=RelatedDialog(db,settings,seed);qtbot.addWidget(dialog);dialog.show()
    qtbot.waitUntil(lambda:dialog.model.rowCount()==1)
    def save():
        time.sleep(.05)
        dialog.repo.override(seed,other,'related','Explicit synthetic test link')
    dialog.mutate(save);dialog.close()
    assert not dialog.closed
    qtbot.waitUntil(lambda:dialog.closed)
    with db.connect() as c:assert c.execute('SELECT note FROM related_overrides').fetchone()[0]=='Explicit synthetic test link'


def test_related_viewer_prepares_ranked_rows_asynchronously(qtbot,db,settings,tmp_path,monkeypatch):
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QDialog
    import app.related_dialog as ui
    p=db.add_entity('person','A');seed=add(db,tmp_path,'seed',p);other=add(db,tmp_path,'other',p,color='pink')
    captured=[]
    class FakeViewer(QDialog):
        favorite_changed=Signal(str,bool)
        related_requested=Signal(str)
        def __init__(self,rows,position,parent,**kwargs):
            super().__init__(parent);self.rows=rows;self.position=position
        def exec(self):
            captured.append([row['id'] for row in self.rows]);return QDialog.DialogCode.Rejected
    monkeypatch.setattr(ui,'Viewer',FakeViewer)
    dialog=ui.RelatedDialog(db,settings,seed);qtbot.addWidget(dialog);dialog.show()
    qtbot.waitUntil(lambda:dialog.model.rowCount()==1)
    dialog.open_selected();assert dialog.opening
    qtbot.waitUntil(lambda:bool(captured))
    assert captured==[[other]] and not dialog.opening
    dialog.close()


def test_local_review_revokes_only_selected_resource(qtbot,db,settings,tmp_path,monkeypatch):
    import app.related_dialog as ui
    p=db.add_entity('person','A');seed=add(db,tmp_path,'seed',p);other=add(db,tmp_path,'other',p,color='pink')
    repo=RelatedRepository(db)
    for operation in ('local_analysis','ai_restore_input'):
        repo.sources.grant('local',operation,'allowed',EVIDENCE)
        for identity in (seed,other):repo.sources.grant('local',operation,'allowed',EVIDENCE,'asset:'+identity)
    original=ui.LocalRightsDialog
    class Review(original):
        def exec(self):
            self.analysis.setChecked(False);self.ai.setChecked(True)
            self.reference.setText(EVIDENCE['reference']);self.note.setText(EVIDENCE['note'])
            return ui.QDialog.DialogCode.Accepted
    monkeypatch.setattr(ui,'LocalRightsDialog',Review)
    dialog=ui.RelatedDialog(db,settings,seed);qtbot.addWidget(dialog);dialog.show()
    qtbot.waitUntil(lambda:dialog.model.rowCount()==1)
    dialog.review_rights(other)
    qtbot.waitUntil(lambda:not dialog.saving)
    assert repo.allowed(seed,('local_analysis','ai_restore_input'))
    assert not repo.allowed(other,('local_analysis',))
    assert repo.allowed(other,('ai_restore_input',))
    dialog.close()
