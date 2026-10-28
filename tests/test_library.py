import threading
from pathlib import Path
import pytest
from PIL import Image
from app.database import Database
from app.core import Settings
from app.images import inspect_image,import_paths,make_thumbnail,file_hash


def test_entity_identity_memberships_and_history(db):
    a=db.add_entity('group','PROJECT',gender='mixed')
    b=db.add_entity('group','SECOND',gender='girl')
    p=db.add_entity('person','Artist','별칭',gender='female',solo=True,group_id=a)
    db.link_member(p,b)
    assert db.members(a)[0]['id']==db.members(b)[0]['id']==p
    db.rename('person',p,'New Name','Alias')
    assert db.entities()['persons'][0]['name']=='New Name'
    with db.connect() as c:
        assert c.execute('SELECT old_name FROM name_history').fetchone()[0]=='Artist'
        assert c.execute('PRAGMA foreign_keys').fetchone()[0]==1
    db.link_member(p,a,unlink=True)
    assert db.members(a)==[]


def test_duplicates_and_invalid_entities(db):
    db.add_entity('person','Same')
    with pytest.raises(ValueError):db.add_entity('person','same')
    with pytest.raises(ValueError):db.add_entity('group','  ')
    with pytest.raises(Exception):db.link_member('missing','missing')


def test_import_links_dedup_and_original_unchanged(db,photo,tmp_path):
    p=db.add_entity('person','솔로','별명',solo=True)
    g=db.add_entity('group','그룹','English')
    db.link_member(p,g)
    before=file_hash(photo)
    first=import_paths(db,[str(photo)],tmp_path/'cache',tmp_path/'originals',False,p,None,threading.Event())
    second=import_paths(db,[str(photo)],tmp_path/'cache',tmp_path/'originals',False,None,g,threading.Event())
    assert first['added']==1 and second['duplicates']==1
    assert db.query(search='별명')[1]==1
    assert db.query(search='English')[1]==1
    assert db.query(('solo',None))[1]==1
    assert db.query(('group',g))[1]==1
    assert db.query(search='%')[1]==0
    assert file_hash(photo)==before
    row=db.query()[0][0]
    db.favorite(row['id'],True)
    assert Database(db.path).query(('favorite',None))[1]==1
    assert db.associations(row['id'])[0][0]['id']==p


def test_group_search_through_membership(db,photo):
    g=db.add_entity('group','Only Membership')
    p=db.add_entity('person','Artist',group_id=g)
    db.register(inspect_image(photo),person=p)
    assert db.query(search='Only Membership')[1]==1
    db.link_member(p,g,target=False)
    assert db.query(('group',g))[1]==0


def test_managed_storage_and_relink(db,photo,tmp_path):
    before=file_hash(photo)
    report=import_paths(db,[str(photo)],tmp_path/'cache',tmp_path/'originals',True,None,None,threading.Event())
    assert report['added']==1
    row=db.query()[0][0]
    assert row['managed']==1 and Path(row['path'])!=photo
    assert file_hash(Path(row['path']))==before
    moved=tmp_path/'moved.png'
    photo.rename(moved)
    db.relink(row['id'],moved,before)
    assert db.query()[0][0]['path']==str(moved)
    bad=tmp_path/'other.png'
    Image.new('RGB',(3,3)).save(bad)
    with pytest.raises(ValueError):db.relink(row['id'],bad,before)


def test_corrupt_missing_unsupported_and_cancellation(db,photo,tmp_path):
    broken=tmp_path/'broken.jpg'
    broken.write_bytes(b'not an image')
    report=import_paths(db,[str(photo),str(broken),str(tmp_path/'missing.jpg')],tmp_path/'cache',tmp_path/'originals',False,None,None,threading.Event())
    assert report['added']==1 and len(report['failed'])==2
    cancel=threading.Event();cancel.set()
    report=import_paths(db,[str(photo)],tmp_path/'cache',tmp_path/'originals',False,None,None,cancel)
    assert report['cancelled'] and report['added']==0


def test_thumbnail_integrity_and_recovery(photo,tmp_path):
    meta=inspect_image(photo)
    before=file_hash(photo)
    output=Path(make_thumbnail(photo,tmp_path/'thumbs',meta['sha256']))
    with Image.open(output) as image:
        assert image.size==(341,512)
    modified=output.stat().st_mtime_ns
    assert make_thumbnail(photo,tmp_path/'thumbs',meta['sha256'])==str(output)
    assert output.stat().st_mtime_ns==modified
    output.write_bytes(b'broken')
    make_thumbnail(photo,tmp_path/'thumbs',meta['sha256'])
    with Image.open(output) as image:image.verify()
    assert file_hash(photo)==before


def test_settings_corruption_and_persistence(tmp_path):
    s=Settings(tmp_path)
    s.values.update(thumbnail_size=300,sort='resolution',sidebar=False)
    s.save()
    assert Settings(tmp_path).values['thumbnail_size']==300
    s.path.write_text('{broken')
    assert Settings(tmp_path).values['thumbnail_size']==216
    s.path.write_text('{"thumbnail_size":"wrong","sort":"wrong","sidebar":null,"cache_path":5}')
    repaired=Settings(tmp_path)
    assert repaired.values['thumbnail_size']==216
    assert repaired.values['sort']=='newest'
    assert repaired.values['sidebar'] is True
    assert repaired.values['cache_path']==''


def test_migration_and_sort_pagination(db,photo):
    meta=inspect_image(photo)
    for i in range(300):
        value=meta|dict(sha256=f'{i:064x}',name=f'{i:04}.png',registered=i,width=i+1)
        db.register(value)
    rows,total=db.query(sort='oldest',offset=256)
    assert len(rows)==44 and total==300 and rows[0]['name']=='0256.png'
    assert db.query(sort='resolution')[0][0]['width']==300
    assert db.query(sort='name')[0][0]['name']=='0000.png'
    again=Database(db.path)
    assert again.query()[1]==300
    with db.connect() as c:assert [r[0] for r in c.execute('SELECT version FROM schema_migrations ORDER BY version')]==[1,2]


def test_exif_orientation_and_cache_invalidation(tmp_path):
    path=tmp_path/'rotated.jpg'
    image=Image.new('RGB',(600,400),'red')
    exif=Image.Exif();exif[274]=6
    image.save(path,exif=exif)
    meta=inspect_image(path)
    assert (meta['width'],meta['height'])==(400,600)
    thumb=Path(make_thumbnail(path,tmp_path/'cache',meta['sha256']))
    with Image.open(thumb) as result:assert result.height>result.width
    Image.new('RGB',(400,200),'blue').save(path)
    make_thumbnail(path,tmp_path/'cache',meta['sha256'])
    with Image.open(thumb) as result:assert result.width>result.height


def test_future_schema_is_not_downgraded(db):
    with db.connect() as conn:conn.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError):Database(db.path)
    with db.connect() as conn:assert conn.execute('PRAGMA user_version').fetchone()[0]==99


def test_locked_database_returns_error_without_losing_data(db):
    import sqlite3
    group=db.add_entity('group','Existing')
    with db.connect() as held:
        held.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError,match='locked'):
            db.add_entity('group','Locked write')
    assert db.entities()['groups'][0]['id']==group


def test_atomic_settings_failure_preserves_existing(tmp_path,monkeypatch):
    s=Settings(tmp_path)
    s.save()
    previous=s.path.read_bytes()
    s.values['thumbnail_size']=300
    original=Path.replace
    def fail(self,target):
        if self==s.path.with_suffix('.tmp'):raise OSError('Simulated disk full')
        return original(self,target)
    monkeypatch.setattr(Path,'replace',fail)
    with pytest.raises(OSError):s.save()
    assert s.path.read_bytes()==previous
