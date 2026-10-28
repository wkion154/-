"""Indexed hybrid metadata, stable source keys and explicit remote relationships."""
# Statements are kept separate: trigger bodies contain semicolons.
BASE='''
CREATE TABLE asset_path_history(asset_id TEXT NOT NULL REFERENCES assets(id),path TEXT NOT NULL,managed INTEGER NOT NULL,recorded REAL NOT NULL,PRIMARY KEY(asset_id,path));
INSERT INTO asset_path_history SELECT id,path,managed,registered FROM assets;
CREATE TABLE gallery_items(id TEXT PRIMARY KEY,kind TEXT NOT NULL,ref_id TEXT NOT NULL,source_id TEXT NOT NULL,name TEXT NOT NULL,sha256 TEXT,width INTEGER NOT NULL DEFAULT 0,height INTEGER NOT NULL DEFAULT 0,provided_width INTEGER,provided_height INTEGER,size INTEGER NOT NULL DEFAULT 0,format TEXT NOT NULL DEFAULT '',registered REAL NOT NULL,captured TEXT,published TEXT,favorite INTEGER NOT NULL DEFAULT 0,linked_asset_id TEXT,page_url TEXT NOT NULL DEFAULT '',image_url TEXT NOT NULL DEFAULT '',thumbnail_url TEXT,role TEXT NOT NULL DEFAULT '',has_preview INTEGER NOT NULL DEFAULT 0,license_status TEXT NOT NULL DEFAULT 'review',hidden INTEGER NOT NULL DEFAULT 0);
CREATE INDEX gallery_newest ON gallery_items(registered DESC,id,kind,hidden,linked_asset_id,sha256);
CREATE INDEX gallery_name ON gallery_items(name COLLATE NOCASE,id,kind,hidden,linked_asset_id,sha256);
CREATE INDEX gallery_pixels ON gallery_items((width*height) DESC,id,kind,hidden,linked_asset_id,sha256);
CREATE INDEX gallery_visibility ON gallery_items(kind,hidden,linked_asset_id,sha256,id);
CREATE INDEX gallery_oldest ON gallery_items(registered,id,kind,hidden,linked_asset_id,sha256);
CREATE INDEX gallery_favorite ON gallery_items(favorite,registered DESC,id,kind,hidden,linked_asset_id,sha256);
CREATE INDEX gallery_kind ON gallery_items(kind,registered DESC,id);
CREATE INDEX gallery_sha ON gallery_items(sha256);
CREATE INDEX gallery_linked ON gallery_items(linked_asset_id);
CREATE TABLE gallery_people(entry_id TEXT NOT NULL REFERENCES gallery_items(id),person_id TEXT NOT NULL REFERENCES persons(id),PRIMARY KEY(entry_id,person_id));
CREATE INDEX gallery_person ON gallery_people(person_id,entry_id);
CREATE TABLE gallery_groups(entry_id TEXT NOT NULL REFERENCES gallery_items(id),group_id TEXT NOT NULL REFERENCES groups(id),PRIMARY KEY(entry_id,group_id));
CREATE INDEX gallery_group ON gallery_groups(group_id,entry_id);
CREATE TABLE gallery_favorites(entry_id TEXT PRIMARY KEY REFERENCES gallery_items(id),favorite INTEGER NOT NULL DEFAULT 0);
CREATE TABLE gallery_identity_history(id INTEGER PRIMARY KEY,entry_id TEXT NOT NULL,revision TEXT,people TEXT NOT NULL,groups_json TEXT NOT NULL,favorite INTEGER NOT NULL,created REAL NOT NULL);
CREATE TABLE gallery_version(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL DEFAULT 0);
INSERT INTO gallery_version VALUES(1,0);
CREATE TABLE remote_view_cache(entry_id TEXT PRIMARY KEY REFERENCES gallery_items(id),path TEXT NOT NULL,sha256 TEXT NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,size INTEGER NOT NULL,format TEXT NOT NULL,source_revision TEXT NOT NULL,verified REAL NOT NULL);
'''

COLUMNS='id,kind,ref_id,source_id,name,sha256,width,height,provided_width,provided_height,size,format,registered,captured,published,favorite,linked_asset_id,page_url,image_url,thumbnail_url,role,has_preview,license_status'
ASSET="a.id,'asset',a.id,'local',a.name,a.sha256,a.width,a.height,a.width,a.height,a.size,a.format,a.registered,a.captured,a.published,a.favorite,NULL,'','',NULL,'',1,'local'"
WEB="'web:'||w.id,'web',w.id,w.source_id,CASE WHEN w.title<>'' THEN w.title ELSE w.url END,w.sha256,COALESCE(w.width,0),COALESCE(w.height,0),w.declared_width,w.declared_height,COALESCE(w.size,0),COALESCE(w.mime,''),w.updated,NULL,p.published,0,w.local_asset_id,p.url,w.url,NULL,w.role,CASE WHEN w.cache_path IS NULL THEN 0 ELSE 1 END,'review'"
COMMONS="'commons:'||r.id,'commons',r.id,'wikimedia',r.title,c.sha256,COALESCE(c.width,0),COALESCE(c.height,0),r.width,r.height,COALESCE(c.size,r.size),COALESCE(c.format,r.mime),r.updated,r.captured,r.published,0,r.local_asset_id,r.source_url,r.original_url,r.thumbnail_url,'thumbnail',CASE WHEN r.thumbnail_path IS NULL THEN 0 ELSE 1 END,r.license_status"


def statements():
    result=[part.strip() for part in BASE.split(';') if part.strip()]
    for table,alias,values,joins in [('assets','a',ASSET,''),('web_images','w',WEB,' JOIN source_pages p ON p.id=w.page_id'),('remote_images','r',COMMONS," LEFT JOIN remote_view_cache c ON c.entry_id='commons:'||r.id AND c.source_revision=COALESCE(r.source_sha1,'')||'|'||r.original_url")]:
        result.append(f'INSERT INTO gallery_items({COLUMNS}) SELECT {values} FROM {table} {alias}{joins}')
        insert=f'INSERT OR IGNORE INTO gallery_items({COLUMNS}) SELECT {values} FROM {table} {alias}{joins} WHERE {alias}.id=NEW.id;'
        # registered is the discovery time, not the last metadata refresh time.
        assignments=','.join(f'{col}=excluded.{col}' for col in COLUMNS.split(',') if col not in ('id','registered','favorite','kind','ref_id'))
        if table=='assets':assignments+=',favorite=excluded.favorite'
        update=f'INSERT INTO gallery_items({COLUMNS}) SELECT {values} FROM {table} {alias}{joins} WHERE {alias}.id=NEW.id ON CONFLICT(id) DO UPDATE SET {assignments};'
        result.append(f'CREATE TRIGGER hybrid_{table}_insert AFTER INSERT ON {table} BEGIN {insert} END')
        result.append(f'CREATE TRIGGER hybrid_{table}_update AFTER UPDATE ON {table} BEGIN {update} END')
    result.append("CREATE TRIGGER hybrid_asset_path_insert AFTER INSERT ON assets BEGIN INSERT OR IGNORE INTO asset_path_history VALUES(NEW.id,NEW.path,NEW.managed,NEW.registered); END")
    result.append("CREATE TRIGGER hybrid_asset_path_update AFTER UPDATE OF path,managed ON assets BEGIN INSERT OR IGNORE INTO asset_path_history VALUES(OLD.id,OLD.path,OLD.managed,CAST(strftime('%s','now') AS REAL)); INSERT OR REPLACE INTO asset_path_history VALUES(NEW.id,NEW.path,NEW.managed,CAST(strftime('%s','now') AS REAL)); END")
    result.extend([
        "CREATE TRIGGER hybrid_favorite AFTER INSERT ON gallery_favorites BEGIN UPDATE gallery_items SET favorite=NEW.favorite WHERE id=NEW.entry_id; UPDATE assets SET favorite=NEW.favorite WHERE id=(SELECT linked_asset_id FROM gallery_items WHERE id=NEW.entry_id); END",
        "CREATE TRIGGER hybrid_favorite_update AFTER UPDATE ON gallery_favorites BEGIN UPDATE gallery_items SET favorite=NEW.favorite WHERE id=NEW.entry_id; UPDATE assets SET favorite=NEW.favorite WHERE id=(SELECT linked_asset_id FROM gallery_items WHERE id=NEW.entry_id); END",
        "CREATE TRIGGER hybrid_promote AFTER UPDATE OF linked_asset_id ON gallery_items WHEN NEW.kind<>'asset' AND NEW.linked_asset_id IS NOT NULL AND OLD.linked_asset_id IS NOT NEW.linked_asset_id BEGIN INSERT OR IGNORE INTO image_person(image_id,person_id) SELECT NEW.linked_asset_id,person_id FROM gallery_people WHERE entry_id=NEW.id; INSERT OR IGNORE INTO image_group(image_id,group_id) SELECT NEW.linked_asset_id,group_id FROM gallery_groups WHERE entry_id=NEW.id; UPDATE assets SET favorite=1 WHERE id=NEW.linked_asset_id AND EXISTS(SELECT 1 FROM gallery_favorites WHERE entry_id=NEW.id AND favorite=1); INSERT INTO asset_visibility(asset_id,hidden,reason) SELECT NEW.linked_asset_id,1,'사용자가 확인한 등장 인물이 모두 남성' WHERE EXISTS(SELECT 1 FROM image_person WHERE image_id=NEW.linked_asset_id) AND NOT EXISTS(SELECT 1 FROM image_person ip JOIN persons p ON p.id=ip.person_id WHERE ip.image_id=NEW.linked_asset_id AND p.gender<>'male') ON CONFLICT(asset_id) DO UPDATE SET hidden=excluded.hidden,reason=excluded.reason; END",
        "CREATE TRIGGER hybrid_cache_insert AFTER INSERT ON remote_view_cache BEGIN UPDATE gallery_items SET width=NEW.width,height=NEW.height,size=NEW.size,format=NEW.format,sha256=NEW.sha256 WHERE id=NEW.entry_id; END",
        "CREATE TRIGGER hybrid_cache_update AFTER UPDATE ON remote_view_cache BEGIN UPDATE gallery_items SET width=NEW.width,height=NEW.height,size=NEW.size,format=NEW.format,sha256=NEW.sha256 WHERE id=NEW.entry_id; END",
        "CREATE TRIGGER hybrid_item_insert AFTER INSERT ON gallery_items BEGIN UPDATE gallery_version SET version=version+1 WHERE id=1; END",
    ])
    hidden="CASE WHEN EXISTS(SELECT 1 FROM gallery_people gp JOIN persons p ON p.id=gp.person_id WHERE gp.entry_id=gallery_items.id AND p.gender='male') AND NOT EXISTS(SELECT 1 FROM gallery_people gp JOIN persons p ON p.id=gp.person_id WHERE gp.entry_id=gallery_items.id AND p.gender<>'male') THEN 1 ELSE 0 END"
    for action,ref in [('INSERT','NEW'),('DELETE','OLD'),('UPDATE','NEW')]:
        result.append(f'CREATE TRIGGER hybrid_people_visibility_{action.lower()} AFTER {action} ON gallery_people BEGIN UPDATE gallery_items SET hidden={hidden} WHERE id={ref}.entry_id; END')
    result.append(f'CREATE TRIGGER hybrid_gender_update AFTER UPDATE OF gender ON persons BEGIN UPDATE gallery_items SET hidden={hidden} WHERE id IN(SELECT entry_id FROM gallery_people WHERE person_id=NEW.id); END')
    differences=' OR '.join(f'OLD.{c} IS NOT NEW.{c}' for c in COLUMNS.split(',') if c!='id')+' OR OLD.hidden IS NOT NEW.hidden'
    result.append(f'CREATE TRIGGER hybrid_item_update AFTER UPDATE ON gallery_items WHEN {differences} BEGIN UPDATE gallery_version SET version=version+1 WHERE id=1; END')
    # Permission and explicit association changes invalidate cached UI authorization.
    for table in ('gallery_people','gallery_groups','gallery_favorites','source_permissions','resource_permissions','source_profiles','provider_reviews','remote_targets','asset_sources'):
        for action in ('INSERT','UPDATE','DELETE'):
            result.append(f'CREATE TRIGGER hybrid_revision_{table}_{action.lower()} AFTER {action} ON {table} BEGIN UPDATE gallery_version SET version=version+1 WHERE id=1; END')
    for table,prefix,column in [('web_images','web:','sha256'),('remote_images','commons:','source_sha1')]:
        snapshot=f"INSERT INTO gallery_identity_history(entry_id,revision,people,groups_json,favorite,created) SELECT '{prefix}'||OLD.id,OLD.{column},(SELECT json_group_array(person_id) FROM gallery_people WHERE entry_id='{prefix}'||OLD.id),(SELECT json_group_array(group_id) FROM gallery_groups WHERE entry_id='{prefix}'||OLD.id),COALESCE((SELECT favorite FROM gallery_items WHERE id='{prefix}'||OLD.id),0),CAST(strftime('%s','now') AS REAL); DELETE FROM gallery_people WHERE entry_id='{prefix}'||OLD.id; DELETE FROM gallery_groups WHERE entry_id='{prefix}'||OLD.id; DELETE FROM gallery_favorites WHERE entry_id='{prefix}'||OLD.id; UPDATE gallery_items SET favorite=0 WHERE id='{prefix}'||OLD.id;"
        result.append(f"CREATE TRIGGER hybrid_identity_{table} AFTER UPDATE OF {column} ON {table} WHEN OLD.{column} IS NOT NULL AND OLD.{column} IS NOT NEW.{column} BEGIN {snapshot} END")
    result.append("CREATE TRIGGER hybrid_page_update AFTER UPDATE OF published,url ON source_pages BEGIN UPDATE gallery_items SET published=NEW.published,page_url=NEW.url WHERE kind='web' AND ref_id IN(SELECT id FROM web_images WHERE page_id=NEW.id); END")
    return result
