import json
import sqlite3
import threading
from pathlib import Path
import pytest
from app.library import LibraryRepository,LibraryService
from app.online_service import OnlineService
from app.images import inspect_image
from app.providers.http import RemoteError
from test_sources import web,indexed,permit,image_bytes
from test_online import FakeProvider,approved,target,png,source


def entry(web):
    service,client,resources=web
    page,item=indexed(web)
    return service,client,resources,'web:'+item['id'],item


def viewed_web(web):
    service,client,resources,identity,item=entry(web)
    resources[item['url']]=(image_bytes((240,360)),'image/png')
    permit(service.repo,'original_download');permit(service.repo,'original_download',identity)
    backend=OnlineService(service.db,service.settings,FakeProvider());backend.web=service
    library=LibraryService(backend)
    resolved=library.original(library.repo.entry(identity),threading.Event())
    return library,service,client,resources,identity,resolved


def test_unified_modes_links_and_actual_dimensions(web,photo,source):
    service,client,resources,identity,item=entry(web);db=service.db
    local=db.register(inspect_image(photo))[0]
    backend=OnlineService(db,service.settings,FakeProvider());_,watch=target(backend.repo)
    backend.repo.index_images(watch,[source],None)
    rows,total=LibraryRepository(db).query();assert total==3
    assert {r['kind'] for r in rows}=={'asset','web','commons'}
    assert all(not r['thumbnail_allowed'] for r in rows if r['kind']!='asset')
    commons=next(r for r in rows if r['kind']=='commons')
    assert commons['width']==commons['height']==0 and commons['provided_width']==source['width']
    assert commons['path']=='' and commons['sha256'] is None
    assert LibraryRepository(db,'local').query()[1]==1
    assert LibraryRepository(db,'online').query()[1]==2
    assert LibraryRepository(db,'online').query(('local',None))[0][0]['id']==local
    assert LibraryRepository(db).query(source_filter=('site','owned'))[1]==1
    assert LibraryRepository(db).query(source_filter=('category','fan'))[1]==1


def test_visible_metadata_never_grants_binary_access(web):
    service,client,resources,identity,item=entry(web)
    backend=OnlineService(service.db,service.settings,FakeProvider());backend.web=service
    library=LibraryService(backend);before=list(client.calls)
    with pytest.raises(RemoteError):library.original(library.repo.entry(identity),threading.Event())
    with pytest.raises(RemoteError):library.preview(library.repo.entry(identity),threading.Event())
    assert client.calls==before
    permit(service.repo,'thumbnail');permit(service.repo,'thumbnail',identity)
    # A regular page image is not an explicit thumbnail; do not fetch its original
    # merely because the row is visible.
    assert not next(r for r in library.repo.query()[0] if r['id']==identity)['thumbnail_allowed']
    with pytest.raises(RemoteError):library.preview(library.repo.entry(identity),threading.Event())
    assert client.calls==before


def test_view_cache_is_separate_from_permanent_storage(web):
    library,service,client,resources,identity,row=viewed_web(web)
    assert row['width']==240 and row['height']==360 and Path(row['path']).is_file()
    assert library.db.query()[1]==0
    assert not library.settings.storage.exists()
    before=len(client.calls);library.original(library.repo.entry(identity),threading.Event())
    assert len(client.calls)==before
    permit(service.repo,'thumbnail');permit(service.repo,'thumbnail',identity)
    row=next(r for r in library.repo.query()[0] if r['id']==identity)
    assert row['thumbnail_allowed'] and not library.preview(row,threading.Event()).isNull()
    service.repo.grant('owned','original_download','denied',{},identity)
    with pytest.raises(RemoteError):library.original(row,threading.Event())


def test_remote_confirmation_favorites_and_selection_survive_preservation(web):
    library,service,client,resources,identity,row=viewed_web(web)
    p=library.db.add_entity('person','Confirmed A',gender='female');q=library.db.add_entity('person','Confirmed B',gender='female')
    group=library.db.add_entity('group','Owned Group')
    library.repo.associate(identity,[p,q],[group]);library.repo.favorite(identity,True)
    assert library.repo.query(('person',p))[1]==1 and library.repo.query(('favorite',None))[1]==1
    before=len(client.calls);job=library.preserve(row)
    assert library.online.run_one(threading.Event(),job_id=job)['state']=='done'
    assert len(client.calls)==before
    rows,total=library.repo.query();assert total==1 and rows[0]['kind']=='asset' and rows[0]['favorite']
    assert library.repo.locate(identity)==0
    people,groups=library.db.associations(rows[0]['id'])
    assert {r['id'] for r in people}=={p,q} and {r['id'] for r in groups}=={group}
    assert Path(rows[0]['path']).is_file() and Path(row['path']).is_file()
    assert library.repo.query(('favorite',None))[1]==1


def test_revision_change_archives_confirmation_and_preserves_saved_photo(web):
    library,service,client,resources,identity,row=viewed_web(web)
    person=library.db.add_entity('person','A',gender='female')
    library.repo.associate(identity,[person],[]);library.repo.favorite(identity,True)
    job=library.preserve(row);library.online.run_one(threading.Event(),job_id=job)
    old=library.db.query()[0][0]
    resources[row['image_url']]=(image_bytes((240,360),color='orange'),'image/png')
    service.probe(row['ref_id'],threading.Event())
    assert library.repo.associations(identity)==([],[])
    assert not library.repo.entry(identity)['favorite']
    assert library.db.query()[0][0]['favorite'] and Path(old['path']).is_file()
    assert library.db.associations(old['id'])[0][0]['id']==person
    assert library.repo.query()[1]==2
    with library.db.connect() as c:
        history=c.execute('SELECT * FROM gallery_identity_history WHERE entry_id=?',(identity,)).fetchone()
        assert json.loads(history['people'])==[person] and history['favorite']==1


def test_mentions_do_not_bind_people_and_male_only_remote_rows_are_hidden(web):
    service,client,resources,identity,item=entry(web);repo=LibraryRepository(service.db)
    person=service.db.add_entity('person','Artist A',gender='male')
    assert repo.query(('person',person))[1]==0
    repo.associate(identity,[person],[])
    assert repo.query()[1]==0
    repo.favorite(identity,True);assert repo.query(('favorite',None))[1]==1
    female=service.db.add_entity('person','B',gender='female');repo.associate(identity,[person,female],[])
    assert repo.query(('person',female))[1]==1
    repo.associate(identity,[person],[])
    with service.db.connect() as c:c.execute("UPDATE persons SET gender='unknown' WHERE id=?",(person,))
    assert repo.query()[1]==1


def test_commons_category_is_group_classification_not_depicted_person(web,source):
    service,_,_=web;backend=OnlineService(service.db,service.settings,FakeProvider());repo=LibraryRepository(service.db)
    person,pwatch=target(backend.repo)
    backend.repo.index_images(pwatch,[source],None)
    assert repo.query(('person',person))[1]==0
    group,gwatch=target(backend.repo,'group','Q5678','Fixture Group')
    backend.repo.index_images(gwatch,[source],None)
    assert repo.query(('group',group))[1]==1
    assert repo.associations('commons:'+backend.repo.images()[0]['id'])==([],[])


def test_commons_view_cache_and_preservation_reuse_verified_bytes(db,settings,png,source):
    provider=FakeProvider();provider.http.data=png
    online=OnlineService(db,settings,provider);approved(online.repo);_,watch=target(online.repo)
    online.repo.index_images(watch,[source],None)
    identity='commons:'+online.repo.images()[0]['id'];library=LibraryService(online)
    row=library.original(library.repo.entry(identity),threading.Event())
    assert row['width']==64 and row['height']==96 and db.query()[1]==0
    assert len(provider.http.calls)==1
    assert library.repo.entry(identity)['width']==64
    library.original(row,threading.Event());assert len(provider.http.calls)==1
    # Reindexing unchanged API metadata must not erase measured dimensions.
    online.repo.index_images(watch,[source],None);assert library.repo.entry(identity)['width']==64
    library.repo.favorite(identity,True)
    job=library.preserve(row);assert online.run_one(threading.Event(),job_id=job)['state']=='done'
    assert len(provider.http.calls)==1 and db.query()[1]==1 and db.query()[0][0]['favorite']
    assert Path(row['path']).is_file()


def test_pagination_sorting_refresh_order_and_invalid_association_rollback(web):
    service,client,resources,identity,item=entry(web);repo=LibraryRepository(service.db)
    person=service.db.add_entity('person','Confirmed')
    repo.associate(identity,[person],[])
    with pytest.raises(sqlite3.IntegrityError):repo.associate(identity,['missing-person'],[])
    assert repo.associations(identity)[0][0]['id']==person
    before=repo.entry(identity)['registered'];version=repo.version()
    with service.db.connect() as c:c.execute('UPDATE web_images SET title=?,updated=updated+99999 WHERE id=?',('New display name',item['id']))
    assert repo.entry(identity)['registered']==before and repo.version()>version
    assert repo.query(search='New display name')[1]==1
    with service.db.connect() as c:
        for i in range(530):
            c.execute("INSERT INTO web_images(id,page_id,source_id,url,logical_key,title,provenance,updated) VALUES(?,?,'owned',?,'fixture',?,'{}',?)",(f'fake-{i:03}',item['page_id'],f'https://cdn.example/{i}',f'Image {i:03}',i))
    first,total=repo.query(sort='name');second,_=repo.query(sort='name',offset=256,count=False)
    assert total==531 and len(first)==len(second)==256
    assert not {r['id'] for r in first}&{r['id'] for r in second}
    assert all(repo.locate(r['id'],sort='name')==i for i,r in enumerate(first[:3]))


def test_hybrid_gallery_links_and_visible_thumbnails_are_async(qtbot,web,photo):
    from app.main_window import MainWindow
    library,service,client,resources,identity,row=viewed_web(web)
    local=library.db.register(inspect_image(photo))[0]
    permit(service.repo,'thumbnail');permit(service.repo,'thumbnail',identity)
    window=MainWindow(library.db,library.settings);window.online_runner.timer.stop();qtbot.addWidget(window)
    window.online_service.web=service;window.library_service=library;window.gallery.thumbnail_loader=library.preview
    window.show();qtbot.waitUntil(lambda:window.model.total==2)
    qtbot.waitUntil(lambda:len(window.gallery.thumbs)==2,timeout=5000)
    remote_position=next(i for i,r in enumerate(window.model.rows) if r['id']==identity)
    window.gallery.setCurrentIndex(window.model.index(remote_position))
    assert window.selected['id']==identity and window.preserve_button.isEnabled() and not window.relink_button.isEnabled()
    qtbot.waitUntil(lambda:'온라인 후보' in window.info_text.text())
    window.toggle_favorite();qtbot.waitUntil(lambda:library.repo.entry(identity)['favorite']==1)
    window.library_mode.setCurrentIndex(window.library_mode.findData('local'))
    qtbot.waitUntil(lambda:window.model.total==1 and not window.model.loading)
    assert window.model.rows[0]['id']==local
    window.close()


def test_viewer_resolves_only_requested_remote_original_and_keeps_order(qtbot,web,photo):
    from app.viewer import Viewer
    library,service,client,resources,identity,row=viewed_web(web)
    library.db.register(inspect_image(photo))
    rows,_=library.repo.query(sort='oldest')
    position=next(i for i,r in enumerate(rows) if r['id']==identity)
    calls=[]
    def resolve(row,cancelled):calls.append(row['id']);return library.original(row,cancelled)
    viewer=Viewer(rows,position,row_resolver=resolve);qtbot.addWidget(viewer);viewer.show()
    qtbot.waitUntil(lambda:viewer.displayed_id==identity)
    assert calls[0]==identity and viewer.canvas.source_width==240
    viewer.actual();qtbot.waitUntil(lambda:abs(viewer.canvas.physical_zoom()-1)<.001)
    assert viewer.total==2 and viewer.rows[viewer.position]['id']==identity
    viewer.close()


def test_stored_remote_permissions_cannot_be_bypassed_by_extra_local_marker(web):
    library,service,client,resources,identity,row=viewed_web(web)
    job=library.preserve(row);library.online.run_one(threading.Event(),job_id=job)
    stored=library.repo.query()[0][0]
    with library.db.connect() as c:c.execute("INSERT OR IGNORE INTO asset_sources(id,asset_id,source_id) VALUES('local-extra',?,'local')",(stored['id'],))
    service.repo.grant('owned','original_download','denied',{},identity)
    service.repo.grant('owned','thumbnail','denied',{},identity)
    assert not library.repo.query()[0][0]['thumbnail_allowed']
    with pytest.raises(RemoteError):library.original(stored,threading.Event())
    with pytest.raises(RemoteError):library.preview(stored,threading.Event())


def test_v5_migration_backfill_and_trigger_rollback(tmp_path,monkeypatch):
    from app.database import Database,SCHEMA
    from app.online_schema import SCHEMA_V2
    from app.source_schema import SCHEMA_V3
    from app.related_schema import SCHEMA_V4
    from app.stability_schema import SCHEMA_V5
    import app.hybrid_schema as schema
    path=tmp_path/'v5.db'
    with sqlite3.connect(path) as c:
        for script in (SCHEMA,SCHEMA_V2,SCHEMA_V3,SCHEMA_V4,SCHEMA_V5):c.executescript(script)
        c.execute("INSERT INTO assets(id,path,name,sha256,width,height,size,format,registered,favorite) VALUES('old','/owned.png','Old','hash',80,120,100,'PNG',10,1)")
        c.execute('PRAGMA user_version=5')
    original=schema.statements;monkeypatch.setattr(schema,'statements',lambda:original()+['NOT VALID SQL'])
    with pytest.raises(sqlite3.Error):Database(path)
    with sqlite3.connect(path) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==5
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='gallery_items'").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'hybrid_%'").fetchone()[0]==0
    assert len(list(tmp_path.glob('*.v5-*.bak')))==1
    monkeypatch.setattr(schema,'statements',original);db=Database(path)
    assert LibraryRepository(db).query()[0][0]['favorite']==1
    assert LibraryRepository(db).query()[0][0]['id']=='old'
    Database(path);assert len(list(tmp_path.glob('*.v5-*.bak')))==2


def test_commons_expired_network_can_still_use_verified_view_cache(db,settings,png,source):
    provider=FakeProvider();provider.http.data=png;online=OnlineService(db,settings,provider);approved(online.repo)
    _,watch=target(online.repo);online.repo.index_images(watch,[source],None)
    library=LibraryService(online);row=library.repo.query()[0][0]
    resolved=library.original(row,threading.Event())
    def gone(*_args,**_kwargs):raise RemoteError('Synthetic expired URL',status=404)
    provider.http.open=gone
    assert library.original(row,threading.Event())['sha256']==resolved['sha256']
    Path(resolved['path']).write_bytes(b'user-modified-cache')
    with pytest.raises(RemoteError):library.original(row,threading.Event())


def test_preservation_promotes_external_duplicate_without_touching_user_file(web,photo):
    service,client,resources,identity,item=entry(web);db=service.db
    existing=db.register(inspect_image(photo))[0];before=photo.read_bytes()
    resources[item['url']]=(before,'image/png')
    permit(service.repo,'original_download');permit(service.repo,'original_download',identity)
    backend=OnlineService(db,service.settings,FakeProvider());backend.web=service;library=LibraryService(backend)
    library.repo.favorite(identity,True)
    library.original(library.repo.entry(identity),threading.Event())
    assert library.repo.query()[1]==2  # An unlinked remote bookmark is not silently hidden.
    job=library.preserve(library.repo.entry(identity));backend.run_one(threading.Event(),job_id=job)
    rows,total=library.repo.query();assert total==1 and rows[0]['id']==existing and rows[0]['favorite']
    assert rows[0]['managed']==1 and Path(rows[0]['path'])!=photo and Path(rows[0]['path']).read_bytes()==before
    assert photo.read_bytes()==before
    with db.connect() as c:assert c.execute('SELECT 1 FROM asset_path_history WHERE asset_id=? AND path=?',(existing,str(photo))).fetchone()


def test_viewer_does_not_prefetch_unopened_remote_originals(qtbot,web,photo):
    from app.page_discovery import discover_html
    from app.viewer import Viewer
    service,client,resources,identity,item=entry(web)
    service.repo.index_page(service.repo.page(item['page_id']),discover_html('<img src="https://cdn.example/b.png">','https://photos.example/page'))
    items=service.repo.images();permit(service.repo,'original_download')
    for value in items:
        resources[value['url']]=(image_bytes((80,120)),'image/png');permit(service.repo,'original_download','web:'+value['id'])
    local=service.db.register(inspect_image(photo))[0]
    online=OnlineService(service.db,service.settings,FakeProvider());online.web=service;library=LibraryService(online)
    rows=[library.repo.entry('web:'+items[0]['id']),library.repo.entry(local),library.repo.entry('web:'+items[1]['id'])]
    before=list(client.calls);viewer=Viewer(rows,1,row_resolver=library.original);qtbot.addWidget(viewer);viewer.show()
    qtbot.waitUntil(lambda:viewer.displayed_id==local)
    viewer.prefetch_neighbors();assert not viewer.prefetch_jobs and client.calls==before
    viewer.step(1);qtbot.waitUntil(lambda:viewer.displayed_id==rows[2]['id'])
    assert [url for url in client.calls[len(before):] if not url.endswith('/robots.txt')]==[items[1]['url']]
    viewer.close()


def test_metadata_refresh_does_not_restore_a_favorite_user_removed(web):
    library,service,client,resources,identity,row=viewed_web(web)
    library.repo.favorite(identity,True)
    job=library.preserve(row);library.online.run_one(threading.Event(),job_id=job)
    stored=library.repo.query()[0][0];library.repo.favorite(stored['id'],False)
    with library.db.connect() as c:c.execute('UPDATE web_images SET title=title||? WHERE id=?',(' · metadata refresh',row['ref_id']))
    assert library.repo.query()[0][0]['favorite']==0
