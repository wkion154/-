import json
import threading
import time
from pathlib import Path
import pytest
from PIL import Image,ImageDraw
from app.images import inspect_image
from app.upscale_service import UpscaleService,OPS
from app.super_resolution import available,upscale,model_path,read_rgb
from app.providers.http import Cancelled
from app.face_reference import available as face_available
face_test=pytest.mark.skipif(not face_available(),reason="Optional MediaPipe face model not installed")

EVIDENCE={'reference':'fixture://restoration-tests-and-NASA-public-domain','note':'Owned charts or NASA/scikit-image public-domain fixture; see tests/fixtures/astronaut-provenance.json. No K-pop accuracy claim.'}
pytestmark=pytest.mark.skipif(not available(),reason='Optional OpenCV/NumPy upscale dependencies not installed')

@pytest.fixture
def service(db,settings):return UpscaleService(db,settings)


def owned(service,tmp_path,name,size=(128,128),image=None,person=None):
    path=tmp_path/(name+'.png')
    (image if image is not None else Image.new('RGB',size,'#765d98')).save(path)
    identity=service.db.register(inspect_image(path),person=person)[0]
    for resource in (None,'asset:'+identity):
        for op in OPS:service.sources.grant('local',op,'allowed',EVIDENCE,resource)
    return identity,path


def textured():
    import numpy as np
    im=Image.new('RGB',(256,256),'#594d6f');draw=ImageDraw.Draw(im);rng=np.random.default_rng(7)
    for _ in range(150):
        x,y=map(int,rng.integers(10,230,2));color=tuple(map(int,rng.integers(10,245,3)));draw.rectangle((x,y,x+10,y+10),fill=color)
    return im


def pair(service,tmp_path):
    person=service.db.add_entity('person','OWNED STUDY')
    high=Image.open(Path(__file__).parent/'fixtures/astronaut.png').convert('RGB');low=high.resize((256,256),Image.Resampling.LANCZOS)
    seed,path=owned(service,tmp_path,'low',image=low,person=person)
    reference,_=owned(service,tmp_path,'high',image=high,person=person)
    return seed,reference,person,path


def test_real_models_scales_tile_boundaries_match_full_inference():
    import numpy as np
    im=np.array(textured().resize((151,143)))
    for scale in (2,4):
        tiled=upscale(im,scale,tile=64);full=upscale(im,scale,tile=256)
        assert tiled.shape==(143*scale,151*scale,3)
        assert np.abs(tiled.astype(int)-full.astype(int)).max()<=1
    with pytest.raises(ValueError):model_path(3)
    with pytest.raises(ValueError):model_path(True)


def test_general_actual_child_process_preserves_original_cache_and_exports(service,tmp_path):
    seed,path=owned(service,tmp_path,'base');before=path.read_bytes()
    result=service.run(seed,threading.Event(),2)
    assert result['metrics']['width']==256 and Path(result['path']).is_file()
    assert result['metrics']['device']=='CPU' and not result['metrics']['identity_preservation_proven']
    assert path.read_bytes()==before and result['rights'][seed]
    reused=service.run(seed,threading.Event(),2);assert reused['reused'] and reused['id']==result['id']
    saved=Path(service.export(seed,result['id'],threading.Event()));assert saved.is_file() and saved.with_suffix('.json').is_file()
    original=Path(service.export_original(seed,threading.Event()));assert original.read_bytes()==before
    service.delete_cache(seed,result['id']);assert not Path(result['path']).exists() and saved.exists() and original.exists()
    assert not service.recent(seed)


@face_test
def test_reference_auto_selection_uses_verified_higher_resolution_pair(service,tmp_path):
    seed,reference,person,path=pair(service,tmp_path)
    result=service.run(seed,threading.Event(),2,'reference')
    assert result['config']['reference_id']==reference and result['config']['person']==person
    evidence=result['metrics']['reference']
    assert evidence['correlation']>=.9 and evidence['inlier_ratio']>=.85 and not evidence['identity_verified']
    assert evidence['outside_mask_unchanged'] and evidence['result_landmark_rms']<=.08
    assert evidence['scope']=='face-interior-only'
    # It really changes the neural result, rather than returning an unused reference ID.
    general=service.run(seed,threading.Event(),2)
    assert Path(result['path']).read_bytes()!=Path(general['path']).read_bytes()


@face_test
def test_unrelated_reference_is_rejected_without_publishing_result(service,tmp_path):
    person=service.db.add_entity('person','CONFIRMED PERSON')
    seed,_=owned(service,tmp_path,'seed',image=textured().resize((128,128)),person=person)
    owned(service,tmp_path,'bad',size=(256,256),person=person)
    with pytest.raises(ValueError,match='보류'):service.run(seed,threading.Event(),2,'reference')
    assert not service.recent(seed)
    with service.db.connect() as c:
        job=json.loads(c.execute("SELECT payload FROM extension_records WHERE kind='upscale_job'").fetchone()[0]);assert job['state']=='failed'


def test_permission_separation_and_before_pixel_access(service,tmp_path,monkeypatch):
    seed,_=owned(service,tmp_path,'rights')
    service.sources.grant('local','result_storage','denied',EVIDENCE,'asset:'+seed)
    monkeypatch.setattr(service,'_snapshot',lambda *_:pytest.fail('No pixels before permission'))
    with pytest.raises(ValueError,match='권한'):service.run(seed,threading.Event())
    assert not service.recent(seed)


def test_hash_change_and_transparent_input_are_rejected(service,tmp_path):
    seed,path=owned(service,tmp_path,'changed');Image.new('RGB',(128,128),'blue').save(path)
    with pytest.raises(ValueError,match='변경'):service.run(seed,threading.Event())
    alpha=Image.new('RGBA',(32,32),(255,0,0,0));a,_=owned(service,tmp_path,'alpha',image=alpha)
    with pytest.raises(ValueError,match='투명'):service.run(a,threading.Event())
    assert not service.recent(a)


def test_revocation_during_worker_prevents_publish_and_export(service,tmp_path,monkeypatch):
    seed,_=owned(service,tmp_path,'revoke');worker=service._worker
    def revoke(*args):
        result=worker(*args);service.sources.grant('local','ai_restore_input','denied',EVIDENCE,'asset:'+seed);return result
    monkeypatch.setattr(service,'_worker',revoke)
    with pytest.raises(ValueError,match='권한'):service.run(seed,threading.Event())
    assert not service.recent(seed) and not list(service.cache.glob('*.png'))
    monkeypatch.setattr(service,'_worker',worker);service.sources.grant('local','ai_restore_input','allowed',EVIDENCE,'asset:'+seed)
    result=service.run(seed,threading.Event());service.sources.grant('local','result_storage','denied',EVIDENCE,'asset:'+seed)
    with pytest.raises(ValueError):service.export(seed,result['id'],threading.Event())
    with pytest.raises(ValueError):service.authorized_result(seed,result['id'],threading.Event())


@face_test
def test_reference_person_connection_is_rechecked_at_publication(service,tmp_path,monkeypatch):
    seed,reference,person,_=pair(service,tmp_path);worker=service._worker
    def unlink(*args):
        result=worker(*args)
        with service.db.connect() as c:c.execute('DELETE FROM image_person WHERE image_id=?',(reference,))
        return result
    monkeypatch.setattr(service,'_worker',unlink)
    with pytest.raises(ValueError,match='보류'):service.run(seed,threading.Event(),2,'reference',person)
    assert not service.recent(seed)


def test_cancel_real_child_process_and_single_flight(service,tmp_path):
    seed,_=owned(service,tmp_path,'cancel',size=(512,512));cancel=threading.Event()
    errors=[]
    def run():
        try:service.run(seed,cancel,4)
        except BaseException as e:errors.append(e)
    thread=threading.Thread(target=run);thread.start()
    deadline=time.monotonic()+3
    while not service.lock.locked() and time.monotonic()<deadline:time.sleep(.005)
    with pytest.raises(ValueError,match='실행 중'):service.run(seed,threading.Event())
    cancel.set();thread.join(5);assert not thread.is_alive() and any(isinstance(e,Cancelled) for e in errors)
    assert not service.recent(seed) and not list(service.cache.glob('work-*'))
    with service.db.connect() as c:
        state=json.loads(c.execute("SELECT payload FROM extension_records WHERE kind='upscale_job'").fetchone()[0])['state'];assert state=='cancelled'


def test_restart_marks_running_jobs_interrupted_and_no_automatic_rerun(service,tmp_path):
    seed,_=owned(service,tmp_path,'restart')
    service._record('interrupted',seed,'upscale_job',dict(state='running'))
    service.recover()
    with service.db.connect() as c:assert json.loads(c.execute('SELECT payload FROM extension_records WHERE id=?',('interrupted',)).fetchone()[0])['state']=='interrupted'
    assert not service.recent(seed)


def test_cached_result_tampering_forces_real_recompute(service,tmp_path):
    seed,_=owned(service,tmp_path,'tamper');result=service.run(seed,threading.Event())
    Path(result['path']).write_bytes(b'not an image')
    newer=service.run(seed,threading.Event());assert not newer['reused'] and newer['id']!=result['id']


def test_output_pixel_limit_prevents_allocation():
    import numpy as np
    with pytest.raises(ValueError,match='한도'):upscale(np.zeros((1001,1001,3),dtype=np.uint8),4)


def test_model_digest_checked_before_native_loader(tmp_path,monkeypatch):
    import app.super_resolution as module
    real=Path.read_bytes
    monkeypatch.setattr(Path,'read_bytes',lambda p:b'changed' if p.suffix=='.pb' else real(p))
    with pytest.raises(ValueError,match='해시'):module.model_path(2)


def test_compare_same_original_coordinates_and_modeless_ui(qtbot,service,tmp_path):
    from PySide6.QtGui import QImage
    from app.upscale_dialog import UpscaleDialog
    seed,_=owned(service,tmp_path,'ui');dialog=UpscaleDialog(service,seed);qtbot.addWidget(dialog);dialog.show()
    qtbot.waitUntil(lambda:dialog.ready)
    assert not dialog.isModal()
    dialog.run('general')
    ticks=[]
    from PySide6.QtCore import QTimer
    timer=QTimer();timer.timeout.connect(lambda:ticks.append(1));timer.start(5)
    qtbot.waitUntil(lambda:not dialog.busy,timeout=15000);timer.stop()
    assert 'general' in dialog.results and len(ticks)>2
    canvas=dialog.canvas;canvas.actual();canvas.center.setX(.3);canvas.center.setY(.6)
    rects=[canvas.image_rect(i) for i in range(3)]
    assert len({r.width() for r in rects})==1 and len({r.top() for r in rects})==1
    assert rects[1].left()-rects[0].left()==pytest.approx(canvas.width()/3)
    dialog.close();assert dialog.closed


def test_unreviewed_remote_origin_never_inherits_local_ai_permission(service,tmp_path,monkeypatch):
    seed,_=owned(service,tmp_path,'remote-origin')
    service.sources.save_spec(dict(id='not-reviewed',name='Unknown source',category='user',adapter='html',domains=['owned.example']))
    with service.db.connect() as c:c.execute("INSERT INTO asset_sources(id,asset_id,source_id,item_id) VALUES('remote-origin',?,'not-reviewed','unreviewed-item')",(seed,))
    monkeypatch.setattr(service,'_snapshot',lambda *_:pytest.fail('Remote rights must be checked before pixels'))
    with pytest.raises(ValueError,match='권한'):service.run(seed,threading.Event())


def test_modeless_cancel_keeps_event_loop_alive_and_closes_cleanly(qtbot,service,tmp_path):
    from app.upscale_dialog import UpscaleDialog
    from PySide6.QtCore import QTimer
    seed,_=owned(service,tmp_path,'large-ui',size=(1000,1000));dialog=UpscaleDialog(service,seed);qtbot.addWidget(dialog);dialog.show()
    qtbot.waitUntil(lambda:dialog.ready);dialog.scale.setCurrentIndex(1)
    ticks=[];timer=QTimer();timer.timeout.connect(lambda:ticks.append(1));timer.start(5)
    dialog.run('general');QTimer.singleShot(40,dialog.close)
    qtbot.waitUntil(lambda:dialog.closed,timeout=8000);timer.stop()
    assert len(ticks)>=3 and not dialog.busy and not service.recent(seed)


def test_publishing_errors_do_not_leave_orphaned_cache(service,tmp_path,monkeypatch):
    seed,_=owned(service,tmp_path,'failed-worker')
    def failed(*_):raise ValueError('simulated backend failure')
    monkeypatch.setattr(service,'_worker',failed)
    with pytest.raises(ValueError,match='backend'):service.run(seed,threading.Event())
    assert not service.recent(seed) and not list(service.cache.glob('work-*')) and not list(service.cache.glob('*.png'))


@face_test
def test_face_only_reference_never_changes_background_or_clothing(service,tmp_path):
    import numpy as np
    seed,reference,person,_=pair(service,tmp_path)
    general=service.run(seed,threading.Event(),2)
    assisted=service.run(seed,threading.Event(),2,'reference',person)
    a=np.array(Image.open(general['path']));b=np.array(Image.open(assisted['path']))
    # Independent generous face rectangle on this fixed NASA 512px fixture.
    # All clothing, scene and hair beyond this box must remain bit-identical.
    outside=np.ones(a.shape[:2],bool);outside[35:195,160:300]=False
    assert np.array_equal(a[outside],b[outside])
    assert np.any(a[~outside]!=b[~outside])


@face_test
def test_face_mesh_rejects_small_face_and_pose_or_expression_mismatch():
    import numpy as np
    from app.face_reference import FaceEngine,register
    high=Image.open(Path(__file__).parent/'fixtures/astronaut.png').convert('RGB')
    with FaceEngine() as engine:
        with pytest.raises(ValueError):engine.detect(np.array(high.resize((128,128))))
        op=engine.detect(np.array(high.resize((256,256))));rp=engine.detect(np.array(high))
        altered=rp.copy();altered[1,0]+=25
        with pytest.raises(ValueError,match='방향|구조'):register(op,altered)
        altered=rp.copy();altered[14,1]+=15
        with pytest.raises(ValueError,match='눈/입'):register(op,altered)


def test_cuda_diagnostics_and_native_oom_cpu_retry(tmp_path,monkeypatch):
    import cv2
    from app import super_resolution as module
    diag=module.diagnostics();assert diag['cpu_ready'] and not diag['vram_measured']
    if not diag['cuda_ready']:
        import numpy as np
        with pytest.raises(ValueError,match='CUDA'):module.upscale(np.zeros((32,32,3),np.uint8),2,backend='cuda')
    image=tmp_path/'cpu-fallback.png';Image.new('RGB',(32,32),'#765d98').save(image)
    import hashlib
    sha=hashlib.sha256(image.read_bytes()).hexdigest();real=module.upscale;attempts=[]
    def fail_cuda(*args,**kwargs):
        attempts.append(kwargs.get('backend'))
        if kwargs.get('backend')=='cuda':raise cv2.error('Simulated CUDA out of memory')
        return real(*args,**kwargs)
    monkeypatch.setattr(module,'upscale',fail_cuda)
    result=module.worker(dict(mode='general',scale=2,backend='cuda',input=dict(path=str(image),sha256=sha),output=str(tmp_path/'out.png')))
    assert attempts==['cuda','cpu'] and result['device']=='CPU' and 'memory' in result['cuda_fallback_reason']


def test_reference_known_distant_capture_date_not_selected(service,tmp_path):
    seed,reference,person,_=pair(service,tmp_path)
    with service.db.connect() as c:
        c.execute("UPDATE assets SET captured='2020-01-01' WHERE id=?",(seed,))
        c.execute("UPDATE assets SET captured='2026-01-01' WHERE id=?",(reference,))
    assert service._candidate(seed,person,threading.Event())==[]
