import json
import sqlite3
import threading
from pathlib import Path
import pytest
from PIL import Image,ImageDraw
from app.database import Database
from app.core import Settings
from app.images import inspect_image
from app.online_service import OnlineService
from app.library import LibraryRepository
from app.analysis_filters import AnalysisFilter
from app.color_analysis import palette,occupancy,rgb_lab,parse_hex
from app.vision_analysis import classify,available,VisionEngine
from app.providers.http import RemoteError,Cancelled
from test_sources import web,indexed,permit,image_bytes

EVIDENCE={'reference':'fixture://owned-generated','note':'Owned synthetic test data, not a real source policy review.'}

@pytest.fixture
def analysis(db,settings):
    settings.values.update(analysis_vision=False,online_enabled=False)
    return OnlineService(db,settings,None)


def own(service,tmp_path,name,color='red',size=(128,128),person=None):
    path=tmp_path/(name+'.png');Image.new('RGB',size,color).save(path)
    identity=service.db.register(inspect_image(path),person=person)[0]
    for resource in (None,'asset:'+identity):service.web.repo.grant('local','local_analysis','allowed',EVIDENCE,resource)
    return identity,path


def test_lab_reference_values_alpha_occupancy_and_masks():
    assert rgb_lab((255,0,0))==pytest.approx((53.2408,80.0925,67.2032),abs=.001)
    assert rgb_lab((255,255,255))==pytest.approx((100,0,0),abs=.001)
    assert rgb_lab((0,0,0))==(0,0,0)
    im=Image.new('RGBA',(128,128),(255,0,0,255));ImageDraw.Draw(im).rectangle((64,0,127,127),fill=(0,0,255,255))
    bins=palette(im)
    assert occupancy(bins,'#ff0000',1)==pytest.approx(50)
    assert occupancy(bins,'#00ff00',1)==0
    ImageDraw.Draw(im).rectangle((64,0,127,127),fill=(0,0,255,0))
    assert occupancy(palette(im),'#ff0000',1)==100
    mask=Image.new('L',im.size,0);assert palette(im,mask)==[]
    assert palette(Image.new('RGBA',(32,32),(255,0,0,0)))==[]


def test_color_query_ranges_zero_percent_unknown_and_sort(analysis,tmp_path):
    service=analysis
    path=tmp_path/'half.png';im=Image.new('RGB',(128,128),'red');ImageDraw.Draw(im).rectangle((64,0,127,127),fill='blue');im.save(path)
    half=service.db.register(inspect_image(path))[0]
    for resource in (None,'asset:'+half):service.web.repo.grant('local','local_analysis','allowed',EVIDENCE,resource)
    red,_=own(service,tmp_path,'red');blue,_=own(service,tmp_path,'blue','blue')
    unknown,_=own(service,tmp_path,'unknown','green')
    for identity in (half,red,blue):service.analysis.run(identity,threading.Event())
    repo=LibraryRepository(service.db,filters=AnalysisFilter(color='#ff0000',tolerance=1,min_share=40,max_share=60))
    rows,total=repo.query();assert total==1 and rows[0]['id']==half and rows[0]['color_share']==pytest.approx(50)
    repo=LibraryRepository(service.db,filters=AnalysisFilter(color='#ff0000',tolerance=1,min_share=0,max_share=0))
    rows,_=repo.query();assert [r['id'] for r in rows]==[blue] and rows[0]['color_share']==0
    repo=LibraryRepository(service.db,filters=AnalysisFilter(color='#ff0000',tolerance=1,min_share=40,include_unknown=True))
    rows,_=repo.query(sort='color_share');ids=[r['id'] for r in rows]
    assert ids.index(red)<ids.index(half) and unknown in ids
    assert next(r for r in rows if r['id']==unknown)['color_share'] is None
    assert repo.locate(red,sort='color_share')==0


def test_analysis_permission_precedes_pixel_access_and_never_grants_ai(analysis,tmp_path,monkeypatch):
    path=tmp_path/'private.png';Image.new('RGB',(16,16),'pink').save(path);identity=analysis.db.register(inspect_image(path))[0]
    def forbidden(*_a,**_k):raise AssertionError('Pixels must not be opened before permission')
    monkeypatch.setattr('app.image_analysis.Image.open',forbidden)
    with pytest.raises(RemoteError):analysis.analysis.run(identity,threading.Event())
    assert not analysis.web.repo.allowed('local','ai_restore_input','asset:'+identity)


def test_incremental_signature_changed_input_and_revoked_filters(analysis,tmp_path):
    identity,path=own(analysis,tmp_path,'input')
    assert analysis.analysis.run(identity,threading.Event())['analysis_updated']
    assert analysis.analysis.run(identity,threading.Event())['reused']
    filters=AnalysisFilter(color='#ff0000',min_share=90,tolerance=1)
    assert LibraryRepository(analysis.db,filters=filters).query()[1]==1
    analysis.web.repo.grant('local','local_analysis','denied',EVIDENCE,'asset:'+identity)
    assert LibraryRepository(analysis.db,filters=filters).query()[1]==0
    assert analysis.analysis.details(identity)['colors']==[]
    analysis.web.repo.grant('local','local_analysis','allowed',EVIDENCE,'asset:'+identity)
    Image.new('RGB',(128,128),'blue').save(path)
    with pytest.raises(RemoteError):analysis.analysis.run(identity,threading.Event())
    with analysis.db.connect() as c:assert c.execute('SELECT stale FROM image_analysis').fetchone()[0]==1
    assert LibraryRepository(analysis.db,filters=filters).query()[1]==0


def test_manual_review_has_priority_history_and_keeps_identity_unchanged(analysis,tmp_path):
    identity,_=own(analysis,tmp_path,'review');analysis.analysis.run(identity,threading.Event())
    analysis.analysis.override(identity,'full_body','single','Human reviewed head and feet in owned fixture')
    repo=LibraryRepository(analysis.db,filters=AnalysisFilter(composition='full_body'))
    assert repo.query()[1]==1
    analysis.analysis.run(identity,threading.Event(),True)
    assert repo.query()[1]==1
    analysis.analysis.override(identity,'back','unknown','Human correction fixture')
    with analysis.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM analysis_override_history').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM image_person').fetchone()[0]==0
    assert repo.query()[1]==0
    assert LibraryRepository(analysis.db,filters=AnalysisFilter(composition='back')).query()[1]==1


def test_positive_landmark_rules_do_not_infer_full_body_from_missing_feet():
    points=[dict(x=.5,y=.4,visibility=.99) for _ in range(33)]
    for i in (0,):points[i]['y']=.12
    for i in (11,12):points[i]['y']=.3
    for i in (23,24):points[i]['y']=.52
    for i in (25,26):points[i]['y']=.73
    for i in (27,28,29,30,31,32):points[i]['y']=.91
    result=classify([],points);assert result['composition']=='full_body' and result['count_class']=='unknown'
    points[31]['visibility']=.2
    assert classify([],points)['composition']!='full_body'
    assert classify([],[])['composition']=='unknown'
    faces=[dict(x=.2,y=.1,width=.5,height=.6,score=.9)]
    assert classify(faces,[])['composition']=='face_extreme'
    assert classify(faces*2,points)['composition']=='unknown' and classify(faces*2,points)['count_class']=='two'


def test_region_color_similarity_is_only_a_candidate(analysis,tmp_path):
    from app.related import RelatedRepository
    person=analysis.db.add_entity('person','Fixture person')
    first,_=own(analysis,tmp_path,'one','red',person=person);second,_=own(analysis,tmp_path,'two','pink',person=person)
    for identity in (first,second):analysis.analysis.run(identity,threading.Event())
    # Owned, explicitly synthetic regional features isolate relation semantics.
    p=palette(Image.new('RGB',(16,16),'blue'))[0]
    with analysis.db.connect() as c:
        for identity in (first,second):c.execute('INSERT INTO analysis_colors(entry_id,region,hex,l,a,b,weight,total) VALUES(?,?,?,?,?,?,?,?)',(identity,'background',p['hex'],p['l'],p['a'],p['b'],p['weight'],p['total']))
    result=RelatedRepository(analysis.db).search(first,threading.Event())['rows'][0]
    assert result['relation']=='appearance_candidate' and '확정 아님' in ' '.join(result['reasons'])
    assert result['appearance'][0]['region']=='background'
    analysis.web.repo.grant('local','local_analysis','denied',EVIDENCE,'asset:'+second)
    result=RelatedRepository(analysis.db).search(first,threading.Event())['rows'][0]
    assert result['relation']=='metadata' and not result['appearance']


def test_remote_analysis_never_downloads_input_and_honors_independent_rights(web):
    service,client,resources=web;page,item=indexed(web)
    online=OnlineService(service.db,service.settings,None);online.web=service;online.settings.values['analysis_vision']=False
    identity='web:'+item['id'];permit(service.repo,'local_analysis');permit(service.repo,'local_analysis',identity)
    before=list(client.calls)
    with pytest.raises(RemoteError):online.analysis.run(identity,threading.Event())
    assert client.calls==before
    resources[item['url']]=(image_bytes((64,96)),'image/png');permit(service.repo,'original_download');permit(service.repo,'original_download',identity)
    service.probe(item['id'],threading.Event());service.repo.grant('owned','original_download','denied',EVIDENCE,identity)
    before=list(client.calls);assert online.analysis.run(identity,threading.Event())['analysis_updated'];assert client.calls==before
    assert not service.repo.allowed('owned','ai_restore_input',identity)


def test_local_analysis_queue_is_independent_and_automatic_is_opt_in(analysis,tmp_path):
    a,_=own(analysis,tmp_path,'a');b,_=own(analysis,tmp_path,'b','blue')
    analysis.analysis.schedule(analysis.repo);assert not analysis.repo.jobs()
    analysis.settings.values['analysis_auto']=True;analysis.analysis.schedule(analysis.repo)
    jobs=analysis.repo.jobs();assert len(jobs)==2 and all(j['kind']=='image_analyze' for j in jobs)
    assert analysis.repo.claim('network') is None
    assert analysis.run_one(threading.Event(),'analysis')['state']=='done'
    assert analysis.run_one(threading.Event(),'analysis')['state']=='done'
    assert analysis.repo.claim('analysis') is None
    assert len({j['job_key'] for j in jobs})==2


def test_filter_validation_and_rtree_cleanup(analysis,tmp_path):
    for kwargs in ({'color':'red'},{'tolerance':float('nan')},{'min_share':80,'max_share':40},{'region':'clothing'},{'composition':'made-up'}):
        with pytest.raises(ValueError):AnalysisFilter(**kwargs)
    identity,_=own(analysis,tmp_path,'cleanup');analysis.analysis.run(identity,threading.Event())
    with analysis.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM color_space').fetchone()[0]>0
        c.execute('DELETE FROM image_analysis WHERE entry_id=?',(identity,))
        assert c.execute('SELECT COUNT(*) FROM color_space').fetchone()[0]==0


def test_modeless_ui_filter_changes_do_not_block_gallery(qtbot,analysis,tmp_path):
    from app.main_window import MainWindow
    identity,_=own(analysis,tmp_path,'ui');analysis.analysis.run(identity,threading.Event())
    window=MainWindow(analysis.db,analysis.settings);window.online_runner.timer.stop();qtbot.addWidget(window);window.show()
    qtbot.waitUntil(lambda:window.model.total==1)
    window.gallery.setCurrentIndex(window.model.index(0));window.toggle_analysis();dialog=window.analysis_dialog
    assert dialog.isVisible() and not dialog.isModal()
    qtbot.waitUntil(lambda:dialog.last is not None)
    dialog.color_enabled.setChecked(True);dialog.color.setText('#ff0000');dialog.minimum.setValue(90);dialog.delta.setValue(1);dialog.apply()
    qtbot.waitUntil(lambda:not window.model.loading and window.model.total==1)
    window.sort.setCurrentIndex(window.sort.findData('color_share'));qtbot.waitUntil(lambda:not window.model.loading)
    assert window.model.rows[0]['color_share']==100
    window.toggle_analysis();assert not dialog.isVisible()
    window.toggle_analysis();assert dialog.isVisible()
    window.close();assert dialog.closed


@pytest.mark.skipif(not available(),reason='Optional MediaPipe analysis package is not installed')
def test_real_offline_cpu_models_run_on_owned_blank_input():
    engine=VisionEngine()
    try:
        result,masks=engine.analyze(Image.new('RGB',(256,384),'pink'),threading.Event())
        assert result['composition']=='unknown' and result['count_class']=='unknown'
        assert not masks and len(result['models'])==4
        assert all(len(h)==64 for h in result['models'].values())
    finally:engine.close()


def test_relinked_identical_bytes_reuse_verified_features_without_pixel_work(analysis,tmp_path,monkeypatch):
    identity,path=own(analysis,tmp_path,'moving');analysis.analysis.run(identity,threading.Event())
    new=tmp_path/'same-bytes.png';new.write_bytes(path.read_bytes());analysis.db.relink(identity,new,inspect_image(path)['sha256'])
    monkeypatch.setattr('app.image_analysis.palette',lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('Same-byte result should be reused')))
    result=analysis.analysis.run(identity,threading.Event())
    assert result['reused'] and result['analysis_updated']
    assert analysis.analysis.details(identity)['colors']


def test_permission_revoked_during_computation_prevents_publication(analysis,tmp_path,monkeypatch):
    import app.image_analysis as module
    identity,_=own(analysis,tmp_path,'revoked');original=module.palette
    def compute(*args,**kwargs):
        result=original(*args,**kwargs)
        analysis.web.repo.grant('local','local_analysis','denied',EVIDENCE,'asset:'+identity)
        return result
    monkeypatch.setattr(module,'palette',compute)
    with pytest.raises(RemoteError):analysis.analysis.run(identity,threading.Event())
    with analysis.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM image_analysis').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM color_space').fetchone()[0]==0


def test_cancelled_and_old_revision_requests_do_not_publish(analysis,tmp_path):
    identity,_=own(analysis,tmp_path,'cancel');cancel=threading.Event();cancel.set()
    with pytest.raises(Cancelled):analysis.analysis.run(identity,cancel)
    with pytest.raises(RemoteError):analysis.analysis.run(identity,threading.Event(),expected_sha='old-revision')
    with analysis.db.connect() as c:assert c.execute('SELECT COUNT(*) FROM image_analysis').fetchone()[0]==0


def test_v6_migration_is_backed_up_and_all_analysis_ddl_rolls_back(tmp_path,monkeypatch):
    from app.database import SCHEMA
    from app.online_schema import SCHEMA_V2
    from app.source_schema import SCHEMA_V3
    from app.related_schema import SCHEMA_V4
    from app.stability_schema import SCHEMA_V5
    from app.hybrid_schema import statements
    import app.analysis_schema as schema
    path=tmp_path/'v6.db'
    with sqlite3.connect(path) as c:
        for script in (SCHEMA,SCHEMA_V2,SCHEMA_V3,SCHEMA_V4,SCHEMA_V5):c.executescript(script)
        for statement in statements():c.execute(statement)
        c.execute("INSERT INTO assets(id,path,name,sha256,width,height,size,format,registered,favorite) VALUES('kept','/owned.png','Kept','sha',20,30,10,'PNG',1,1)")
        c.execute('PRAGMA user_version=6')
    original=schema.migrate
    def broken(conn):original(conn);conn.execute('INVALID SQL')
    monkeypatch.setattr(schema,'migrate',broken)
    with pytest.raises(sqlite3.Error):Database(path)
    with sqlite3.connect(path) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==6
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='image_analysis' OR name LIKE 'color_space%'").fetchone()[0]==0
    assert len(list(tmp_path.glob('*.v6-*.bak')))==1
    monkeypatch.setattr(schema,'migrate',original);db=Database(path)
    with db.connect() as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==7
        assert c.execute("SELECT favorite FROM assets WHERE id='kept'").fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM image_analysis').fetchone()[0]==0
    Database(path);assert len(list(tmp_path.glob('*.v6-*.bak')))==2
