"""Additive schema v2: remote indexes never masquerade as local originals."""
SCHEMA_V2 = '''
CREATE TABLE provider_reviews(provider TEXT PRIMARY KEY, reviewed_at REAL, evidence TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE entity_external_ids(provider TEXT NOT NULL, external_id TEXT NOT NULL, kind TEXT NOT NULL, entity_id TEXT NOT NULL, PRIMARY KEY(provider,external_id));
CREATE TABLE artist_candidates(id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', reason TEXT NOT NULL DEFAULT '', updated REAL NOT NULL, UNIQUE(provider,external_id));
CREATE TABLE watch_targets(id TEXT PRIMARY KEY, kind TEXT NOT NULL, entity_id TEXT NOT NULL, external_id TEXT NOT NULL, category TEXT, last_success REAL, next_due REAL NOT NULL DEFAULT 0, cursor TEXT, active INTEGER NOT NULL DEFAULT 1, UNIQUE(kind,entity_id));
CREATE TABLE remote_images(id TEXT PRIMARY KEY, provider TEXT NOT NULL, source_id TEXT NOT NULL, source_url TEXT NOT NULL, original_url TEXT NOT NULL, thumbnail_url TEXT, title TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, size INTEGER NOT NULL, mime TEXT NOT NULL, source_sha1 TEXT, license_name TEXT, license_url TEXT, attribution TEXT, restrictions TEXT, license_status TEXT NOT NULL, captured TEXT, published TEXT, thumbnail_path TEXT, local_asset_id TEXT REFERENCES assets(id), updated REAL NOT NULL, payload TEXT NOT NULL, UNIQUE(provider,source_id));
CREATE TABLE remote_targets(remote_id TEXT REFERENCES remote_images(id), target_id TEXT REFERENCES watch_targets(id), PRIMARY KEY(remote_id,target_id));
CREATE TABLE sync_jobs(id TEXT PRIMARY KEY, job_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0, next_run REAL NOT NULL DEFAULT 0, updated REAL NOT NULL, message TEXT NOT NULL DEFAULT '', result TEXT);
CREATE INDEX remote_updated ON remote_images(updated DESC,id);
CREATE INDEX remote_local_asset ON remote_images(local_asset_id);
CREATE INDEX candidate_review ON artist_candidates(status,updated DESC);
CREATE INDEX sync_updated ON sync_jobs(updated DESC);
CREATE INDEX remote_target_index ON remote_targets(target_id,remote_id);
CREATE INDEX sync_ready ON sync_jobs(state,next_run,updated);
'''
