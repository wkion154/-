"""Schema v5: source scheduling, claim ownership and incremental hash index."""
SCHEMA_V5='''
ALTER TABLE sync_jobs ADD COLUMN source_key TEXT NOT NULL DEFAULT 'wikimedia';
ALTER TABLE sync_jobs ADD COLUMN claim_token TEXT;
CREATE TABLE sync_sources(source_key TEXT PRIMARY KEY,next_run REAL NOT NULL DEFAULT 0,last_claim REAL NOT NULL DEFAULT 0);
CREATE INDEX sync_source_turn ON sync_sources(last_claim);
UPDATE sync_jobs SET source_key='web:'||COALESCE((SELECT p.source_id FROM source_pages p WHERE p.id=json_extract(sync_jobs.payload,'$.page_id')),(SELECT w.source_id FROM web_images w WHERE w.id=json_extract(sync_jobs.payload,'$.item_id')),'unknown') WHERE kind LIKE 'web_%';
INSERT INTO sync_sources(source_key) SELECT DISTINCT source_key FROM sync_jobs;
CREATE INDEX sync_source_ready ON sync_jobs(source_key,state,next_run,updated);
CREATE TABLE perceptual_features(item_id TEXT PRIMARY KEY REFERENCES web_images(id),sha256 TEXT NOT NULL,algorithm TEXT NOT NULL,b0 INTEGER NOT NULL,b1 INTEGER NOT NULL,b2 INTEGER NOT NULL,b3 INTEGER NOT NULL,b4 INTEGER NOT NULL,updated REAL NOT NULL);
CREATE INDEX perceptual_b0 ON perceptual_features(algorithm,b0,item_id);
CREATE INDEX perceptual_b1 ON perceptual_features(algorithm,b1,item_id);
CREATE INDEX perceptual_b2 ON perceptual_features(algorithm,b2,item_id);
CREATE INDEX perceptual_b3 ON perceptual_features(algorithm,b3,item_id);
CREATE INDEX perceptual_b4 ON perceptual_features(algorithm,b4,item_id);
'''
