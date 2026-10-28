"""Schema v3, additive multi-source discovery and independent permissions."""
SCHEMA_V3='''
CREATE TABLE source_profiles(id TEXT PRIMARY KEY,name TEXT NOT NULL,category TEXT NOT NULL,adapter TEXT NOT NULL,spec TEXT NOT NULL,digest TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,updated REAL NOT NULL);
CREATE TABLE source_permissions(source_id TEXT NOT NULL REFERENCES source_profiles(id),operation TEXT NOT NULL,state TEXT NOT NULL,evidence TEXT NOT NULL,profile_digest TEXT NOT NULL,checked REAL NOT NULL,PRIMARY KEY(source_id,operation));
CREATE TABLE resource_permissions(resource_id TEXT NOT NULL,operation TEXT NOT NULL,state TEXT NOT NULL,evidence TEXT NOT NULL,checked REAL NOT NULL,managed_by TEXT NOT NULL DEFAULT 'manual',PRIMARY KEY(resource_id,operation));
CREATE TABLE source_pages(id TEXT PRIMARY KEY,source_id TEXT NOT NULL REFERENCES source_profiles(id),url TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',mode TEXT NOT NULL DEFAULT 'html',last_success REAL,next_due REAL NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'linked',published TEXT,metadata TEXT NOT NULL DEFAULT '{}',message TEXT NOT NULL DEFAULT '',UNIQUE(source_id,url));
CREATE TABLE web_images(id TEXT PRIMARY KEY,page_id TEXT NOT NULL REFERENCES source_pages(id),source_id TEXT NOT NULL REFERENCES source_profiles(id),url TEXT NOT NULL,logical_key TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',role TEXT NOT NULL DEFAULT 'page_image',declared_width INTEGER,declared_height INTEGER,width INTEGER,height INTEGER,size INTEGER,mime TEXT,sha256 TEXT,dhash TEXT,cache_path TEXT,local_asset_id TEXT REFERENCES assets(id),provenance TEXT NOT NULL,updated REAL NOT NULL,UNIQUE(page_id,url));
CREATE TABLE web_revisions(item_id TEXT NOT NULL REFERENCES web_images(id),sha256 TEXT NOT NULL,path TEXT NOT NULL,width INTEGER NOT NULL,height INTEGER NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,dhash TEXT,asset_id TEXT REFERENCES assets(id),created REAL NOT NULL,evidence TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(item_id,sha256));
CREATE TABLE web_match_proposals(item_id TEXT NOT NULL REFERENCES web_images(id),kind TEXT NOT NULL,entity_id TEXT NOT NULL,evidence TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'unconfirmed',PRIMARY KEY(item_id,kind,entity_id));
CREATE TABLE image_relations(left_id TEXT NOT NULL,right_id TEXT NOT NULL,kind TEXT NOT NULL,score REAL,evidence TEXT NOT NULL,PRIMARY KEY(left_id,right_id,kind));
CREATE TABLE source_robots(source_id TEXT NOT NULL,host TEXT NOT NULL,body TEXT NOT NULL,checked REAL NOT NULL,PRIMARY KEY(source_id,host));
CREATE TABLE asset_sources(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(id),source_id TEXT NOT NULL,item_id TEXT NOT NULL DEFAULT '',page_url TEXT NOT NULL DEFAULT '',image_url TEXT NOT NULL DEFAULT '',license_url TEXT NOT NULL DEFAULT '',attribution TEXT NOT NULL DEFAULT '',UNIQUE(asset_id,source_id,item_id));
CREATE TABLE asset_visibility(asset_id TEXT PRIMARY KEY REFERENCES assets(id),hidden INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '');
CREATE INDEX web_source_updated ON web_images(source_id,updated DESC,id);
CREATE INDEX web_page ON web_images(page_id,updated DESC,id);
CREATE INDEX web_sha256 ON web_images(sha256);
CREATE INDEX source_asset ON asset_sources(asset_id,source_id);
CREATE INDEX source_site_asset ON asset_sources(source_id,asset_id);
INSERT INTO asset_sources(id,asset_id,source_id) SELECT 'legacy-local:'||a.id,a.id,'local' FROM assets a WHERE a.managed=0 OR NOT EXISTS(SELECT 1 FROM remote_images r WHERE r.local_asset_id=a.id);
'''
