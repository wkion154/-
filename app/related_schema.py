"""Additive schema for explained relationships; never modifies originals."""
SCHEMA_V4 = '''
CREATE TABLE related_overrides(left_id TEXT NOT NULL REFERENCES assets(id),right_id TEXT NOT NULL REFERENCES assets(id),kind TEXT NOT NULL,note TEXT NOT NULL,updated REAL NOT NULL,PRIMARY KEY(left_id,right_id),CHECK(left_id<right_id));
CREATE INDEX related_reverse ON related_overrides(right_id,left_id);
CREATE TABLE related_searches(seed_id TEXT PRIMARY KEY REFERENCES assets(id),version TEXT NOT NULL,created REAL NOT NULL,payload TEXT NOT NULL);
CREATE INDEX web_asset_related ON web_images(local_asset_id);
CREATE INDEX image_relations_reverse ON image_relations(right_id,left_id);
CREATE INDEX assets_capture_related ON assets(captured,id);
CREATE INDEX assets_publish_related ON assets(published,id);
'''
