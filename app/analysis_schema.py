"""Schema v7: immutable-input analysis, perceptual color index, review overrides."""
BASE='''
CREATE TABLE image_analysis(entry_id TEXT PRIMARY KEY REFERENCES gallery_items(id),sha256 TEXT NOT NULL,engine TEXT NOT NULL,signature TEXT NOT NULL,composition TEXT NOT NULL DEFAULT 'unknown',confidence REAL NOT NULL DEFAULT 0,count_class TEXT NOT NULL DEFAULT 'unknown',face_count INTEGER,region_status TEXT NOT NULL DEFAULT 'unavailable',payload TEXT NOT NULL,stale INTEGER NOT NULL DEFAULT 0,analyzed REAL NOT NULL);
CREATE INDEX analysis_composition ON image_analysis(composition,stale,entry_id);
CREATE INDEX analysis_hash ON image_analysis(sha256,engine);
CREATE TABLE analysis_colors(id INTEGER PRIMARY KEY,entry_id TEXT NOT NULL REFERENCES image_analysis(entry_id) ON DELETE CASCADE,region TEXT NOT NULL,hex TEXT NOT NULL,l REAL NOT NULL,a REAL NOT NULL,b REAL NOT NULL,weight INTEGER NOT NULL,total INTEGER NOT NULL);
CREATE INDEX colors_entry ON analysis_colors(entry_id,region);
CREATE TABLE analysis_overrides(entry_id TEXT PRIMARY KEY REFERENCES gallery_items(id),sha256 TEXT NOT NULL,composition TEXT NOT NULL,count_class TEXT NOT NULL,note TEXT NOT NULL,updated REAL NOT NULL);
CREATE TABLE analysis_override_history(id INTEGER PRIMARY KEY,entry_id TEXT NOT NULL,sha256 TEXT NOT NULL,composition TEXT NOT NULL,count_class TEXT NOT NULL,note TEXT NOT NULL,updated REAL NOT NULL);
CREATE TABLE analysis_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
'''


def migrate(conn):
    for statement in BASE.split(';'):
        if statement.strip():conn.execute(statement)
    import sqlite3
    try:
        conn.execute('CREATE VIRTUAL TABLE color_space USING rtree(id,l0,l1,a0,a1,b0,b1)')
        kind='rtree'
    except sqlite3.OperationalError:
        conn.execute('CREATE TABLE color_space(id INTEGER PRIMARY KEY,l0 REAL,l1 REAL,a0 REAL,a1 REAL,b0 REAL,b1 REAL)')
        conn.execute('CREATE INDEX color_space_l ON color_space(l0,l1)');kind='btree_fallback'
    conn.execute('INSERT INTO analysis_meta VALUES(?,?)',('color_index',kind))
    for action in ('UPDATE','DELETE'):
        conn.execute(f'CREATE TRIGGER analysis_override_archive_{action.lower()} BEFORE {action} ON analysis_overrides BEGIN INSERT INTO analysis_override_history(entry_id,sha256,composition,count_class,note,updated) VALUES(OLD.entry_id,OLD.sha256,OLD.composition,OLD.count_class,OLD.note,OLD.updated); END')
    conn.execute('CREATE TRIGGER color_insert AFTER INSERT ON analysis_colors BEGIN INSERT INTO color_space VALUES(NEW.id,NEW.l,NEW.l,NEW.a,NEW.a,NEW.b,NEW.b); END')
    conn.execute('CREATE TRIGGER color_delete AFTER DELETE ON analysis_colors BEGIN DELETE FROM color_space WHERE id=OLD.id; END')
    for table in ('image_analysis','analysis_overrides'):
        for action in ('INSERT','UPDATE','DELETE'):
            conn.execute(f'CREATE TRIGGER analysis_revision_{table}_{action.lower()} AFTER {action} ON {table} BEGIN UPDATE gallery_version SET version=version+1 WHERE id=1; END')
