"""Connection-per-operation SQLite repositories; UI queries run on worker threads."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
import uuid

SCHEMA = '''
CREATE TABLE IF NOT EXISTS groups(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'girl', aliases TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS persons(id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '', gender TEXT NOT NULL DEFAULT 'unknown', solo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS memberships(person_id TEXT REFERENCES persons(id), group_id TEXT REFERENCES groups(id), started TEXT, ended TEXT, gallery_target INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(person_id,group_id));
CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, width INTEGER NOT NULL, height INTEGER NOT NULL, size INTEGER NOT NULL, format TEXT NOT NULL, registered REAL NOT NULL, captured TEXT, published TEXT, managed INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS image_person(image_id TEXT REFERENCES assets(id), person_id TEXT REFERENCES persons(id), status TEXT NOT NULL DEFAULT 'user_confirmed', source TEXT NOT NULL DEFAULT 'manual', PRIMARY KEY(image_id,person_id));
CREATE TABLE IF NOT EXISTS image_group(image_id TEXT REFERENCES assets(id), group_id TEXT REFERENCES groups(id), PRIMARY KEY(image_id,group_id));
CREATE TABLE IF NOT EXISTS external_identifiers(person_id TEXT REFERENCES persons(id), provider TEXT NOT NULL, external_id TEXT NOT NULL, UNIQUE(provider,external_id));
CREATE TABLE IF NOT EXISTS extension_records(id TEXT PRIMARY KEY, image_id TEXT REFERENCES assets(id), kind TEXT NOT NULL, provider TEXT, version TEXT, payload TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS name_history(id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL, old_name TEXT NOT NULL, changed REAL NOT NULL);
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied REAL NOT NULL);
CREATE INDEX IF NOT EXISTS assets_date ON assets(registered DESC,id);
CREATE INDEX IF NOT EXISTS assets_name ON assets(name COLLATE NOCASE,id);
CREATE INDEX IF NOT EXISTS assets_resolution ON assets((width*height) DESC,id);
CREATE INDEX IF NOT EXISTS assets_favorite ON assets(favorite,registered DESC);
CREATE INDEX IF NOT EXISTS image_person_person ON image_person(person_id,image_id);
CREATE INDEX IF NOT EXISTS image_group_group ON image_group(group_id,image_id);
'''


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from .online_schema import SCHEMA_V2
        from .source_schema import SCHEMA_V3
        from .related_schema import SCHEMA_V4
        from .stability_schema import SCHEMA_V5
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 6:
                raise RuntimeError('이 라이브러리는 더 최신 버전의 앱에서 생성되었습니다.')
            # SQLite backup includes WAL contents; never replace an existing user backup.
            if version in (1,2,3,4,5):
                backup_path = self.path.with_name(self.path.name+f'.v{version}-'+uuid.uuid4().hex[:12]+'.bak')
                destination = sqlite3.connect(backup_path)
                try:
                    db.backup(destination)
                finally:
                    destination.close()
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE')
            for statement in SCHEMA.split(';'):
                if statement.strip():db.execute(statement)
            db.execute('INSERT OR IGNORE INTO schema_migrations VALUES(1,?)', (time.time(),))
            if version < 2:
                for statement in SCHEMA_V2.split(';'):
                    if statement.strip():db.execute(statement)
                db.execute('INSERT INTO schema_migrations VALUES(2,?)',(time.time(),))
            if version < 3:
                for statement in SCHEMA_V3.split(';'):
                    if statement.strip():db.execute(statement)
                db.execute('INSERT INTO schema_migrations VALUES(3,?)',(time.time(),))
            if version < 4:
                for statement in SCHEMA_V4.split(';'):
                    if statement.strip():db.execute(statement)
                db.execute('INSERT INTO schema_migrations VALUES(4,?)',(time.time(),))
            if version < 5:
                for statement in SCHEMA_V5.split(';'):
                    if statement.strip():db.execute(statement)
                from .perceptual import backfill
                backfill(db)
                db.execute('INSERT INTO schema_migrations VALUES(5,?)',(time.time(),))
            if version < 6:
                from .hybrid_schema import statements
                for statement in statements():db.execute(statement)
                db.execute('INSERT INTO schema_migrations VALUES(6,?)',(time.time(),))
            db.execute('PRAGMA user_version=6')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=3)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def entities(self):
        with self.connect() as db:
            return {table: [dict(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY name COLLATE NOCASE')] for table in ('groups', 'persons')}

    def add_entity(self, kind, name, aliases='', gender='unknown', solo=False, group_id=None, gallery_target=True):
        name = name.strip()
        if not name:
            raise ValueError('이름을 입력하세요.')
        table = 'groups' if kind == 'group' else 'persons'
        with self.connect() as db:
            # Same names are candidates, never automatically merge two identities.
            if db.execute(f'SELECT 1 FROM {table} WHERE name=? COLLATE NOCASE', (name,)).fetchone():
                raise ValueError('같은 이름이 있습니다. 동명이인은 구별되는 표시 이름을 사용하세요.')
            identity = uuid.uuid4().hex
            if kind == 'group':
                db.execute('INSERT INTO groups VALUES(?,?,?,?)', (identity, name, gender, aliases))
            else:
                db.execute('INSERT INTO persons VALUES(?,?,?,?,?)', (identity, name, aliases, gender, int(solo)))
                if group_id:
                    db.execute('INSERT INTO memberships(person_id,group_id,gallery_target) VALUES(?,?,?)', (identity, group_id, int(gallery_target)))
            return identity

    def rename(self, kind, identity, name, aliases):
        name = name.strip()
        if not name:
            raise ValueError('이름을 입력하세요.')
        table = 'groups' if kind == 'group' else 'persons'
        with self.connect() as db:
            if db.execute(f'SELECT 1 FROM {table} WHERE name=? COLLATE NOCASE AND id<>?', (name, identity)).fetchone():
                raise ValueError('같은 이름이 이미 있습니다.')
            row = db.execute(f'SELECT name FROM {table} WHERE id=?', (identity,)).fetchone()
            if not row:
                raise ValueError('항목을 찾을 수 없습니다.')
            if row['name'] != name:
                db.execute('INSERT INTO name_history(entity_id,old_name,changed) VALUES(?,?,?)', (identity, row['name'], time.time()))
            db.execute(f'UPDATE {table} SET name=?,aliases=? WHERE id=?', (name, aliases, identity))

    def link_member(self, person, group, target=True, unlink=False):
        with self.connect() as db:
            if unlink:
                db.execute('DELETE FROM memberships WHERE person_id=? AND group_id=?', (person, group))
            else:
                db.execute('INSERT INTO memberships(person_id,group_id,gallery_target) VALUES(?,?,?) ON CONFLICT(person_id,group_id) DO UPDATE SET gallery_target=excluded.gallery_target', (person, group, int(target)))

    def members(self, group):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT p.*,m.gallery_target FROM persons p JOIN memberships m ON m.person_id=p.id WHERE m.group_id=? ORDER BY p.name', (group,))]

    def register(self, metadata, person=None, group=None, origin='local'):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id,path,managed FROM assets WHERE sha256=?', (metadata['sha256'],)).fetchone()
            duplicate = row is not None
            identity = row['id'] if duplicate else uuid.uuid4().hex
            if not duplicate:
                keys = ('path', 'name', 'sha256', 'width', 'height', 'size', 'format', 'registered', 'managed')
                db.execute('INSERT INTO assets(id,'+','.join(keys)+') VALUES('+','.join('?' for _ in range(10))+')', (identity, *(metadata[k] for k in keys)))
            elif metadata.get('managed') and not row['managed']:
                # Explicit permanent preservation may promote an external reference.
                # The old file is untouched; schema-v6 history retains its location.
                db.execute('UPDATE assets SET path=?,managed=1 WHERE id=?',(metadata['path'],identity))
            if person:
                db.execute('INSERT OR IGNORE INTO image_person(image_id,person_id) VALUES(?,?)', (identity, person))
            if group:
                db.execute('INSERT OR IGNORE INTO image_group VALUES(?,?)', (identity, group))
            if origin:
                db.execute('INSERT OR IGNORE INTO asset_sources(id,asset_id,source_id) VALUES(?,?,?)',(uuid.uuid4().hex,identity,origin))
            return identity, duplicate

    def _selection(self, scope, search, sort, source_filter=None):
        where, args = [], []
        if not scope or scope[0] not in ('local','favorite'):
            where.append("a.id NOT IN (SELECT v.asset_id FROM asset_visibility v WHERE v.hidden=1 AND v.asset_id IS NOT NULL AND EXISTS(SELECT 1 FROM image_person ip JOIN persons p ON p.id=ip.person_id WHERE ip.image_id=v.asset_id AND p.gender='male') AND NOT EXISTS(SELECT 1 FROM image_person ip JOIN persons p ON p.id=ip.person_id WHERE ip.image_id=v.asset_id AND p.gender<>'male'))")
        if source_filter:
            filter_kind,value=source_filter
            if filter_kind=='site':
                where.append('(EXISTS(SELECT 1 FROM asset_sources s WHERE s.asset_id=a.id AND s.source_id=?)'+(" OR EXISTS(SELECT 1 FROM remote_images r WHERE r.local_asset_id=a.id)" if value=='wikimedia' else '')+')')
                args.append(value)
            elif filter_kind=='category':
                extra=" OR EXISTS(SELECT 1 FROM remote_images r WHERE r.local_asset_id=a.id)" if value=='fan' else (" OR EXISTS(SELECT 1 FROM asset_sources s WHERE s.asset_id=a.id AND s.source_id='local')" if value=='user' else '')
                where.append('(EXISTS(SELECT 1 FROM asset_sources s JOIN source_profiles p ON p.id=s.source_id WHERE s.asset_id=a.id AND p.category=?)'+extra+')')
                args.append(value)
        if scope:
            kind, value = scope
            if kind == 'favorite':
                where.append('a.favorite=1')
            elif kind == 'person':
                where.append('EXISTS(SELECT 1 FROM image_person ip WHERE ip.image_id=a.id AND ip.person_id=?)')
                args.append(value)
            elif kind == 'solo':
                where.append('EXISTS(SELECT 1 FROM image_person ip JOIN persons p ON p.id=ip.person_id WHERE ip.image_id=a.id AND p.solo=1)')
            elif kind == 'group' and value is None:
                where.append('(EXISTS(SELECT 1 FROM image_group ig WHERE ig.image_id=a.id) OR EXISTS(SELECT 1 FROM image_person ip JOIN memberships m ON m.person_id=ip.person_id WHERE ip.image_id=a.id AND m.gallery_target=1))')
            elif kind == 'group':
                where.append('(EXISTS(SELECT 1 FROM image_group ig WHERE ig.image_id=a.id AND ig.group_id=?) OR EXISTS(SELECT 1 FROM image_person ip JOIN memberships m ON m.person_id=ip.person_id WHERE ip.image_id=a.id AND m.group_id=? AND m.gallery_target=1))')
                args += [value, value]
        if search.strip():
            term = '%' + search.strip().replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
            where.append("(a.name LIKE ? ESCAPE '!' OR EXISTS(SELECT 1 FROM image_person ip JOIN persons p ON p.id=ip.person_id WHERE ip.image_id=a.id AND (p.name LIKE ? ESCAPE '!' OR p.aliases LIKE ? ESCAPE '!')) OR EXISTS(SELECT 1 FROM image_group ig JOIN groups g ON g.id=ig.group_id WHERE ig.image_id=a.id AND (g.name LIKE ? ESCAPE '!' OR g.aliases LIKE ? ESCAPE '!')) OR EXISTS(SELECT 1 FROM image_person ip JOIN memberships m ON m.person_id=ip.person_id JOIN groups g ON g.id=m.group_id WHERE ip.image_id=a.id AND (g.name LIKE ? ESCAPE '!' OR g.aliases LIKE ? ESCAPE '!')))" )
            args += [term] * 7
        orders = {'newest': 'a.registered DESC,a.id', 'oldest': 'a.registered,a.id', 'name': 'a.name COLLATE NOCASE,a.id', 'resolution': '(a.width*a.height) DESC,a.id'}
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        return clause, args, orders.get(sort,orders['newest'])

    def query(self, scope=None, search='', sort='newest', offset=0, limit=256, count=True, source_filter=None):
        clause,args,order = self._selection(scope,search,sort,source_filter)
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM assets a'+clause, args).fetchone()[0] if count else None
            rows = [dict(r) for r in db.execute('SELECT a.* FROM assets a'+clause+' ORDER BY '+order+' LIMIT ? OFFSET ?', (*args, limit, offset))]
            return rows, total

    def locate(self, identity, scope=None, search='', sort='newest', source_filter=None):
        """Find the ordered position of a stable ID without transferring all rows."""
        clause,args,order = self._selection(scope,search,sort,source_filter)
        with self.connect() as db:
            row = db.execute('SELECT position FROM (SELECT a.id,ROW_NUMBER() OVER (ORDER BY '+order+')-1 AS position FROM assets a'+clause+') WHERE id=?',(*args,identity)).fetchone()
            return row['position'] if row else None

    def favorite(self, identity, value):
        with self.connect() as db:
            db.execute('UPDATE assets SET favorite=? WHERE id=?', (int(value), identity))

    def associations(self, identity):
        with self.connect() as db:
            people = [dict(r) for r in db.execute('SELECT p.* FROM persons p JOIN image_person ip ON ip.person_id=p.id WHERE ip.image_id=?', (identity,))]
            groups = [dict(r) for r in db.execute('SELECT g.* FROM groups g JOIN image_group ig ON ig.group_id=g.id WHERE ig.image_id=?', (identity,))]
            return people, groups

    def relink(self, identity, path, expected_hash):
        from .images import file_hash
        if file_hash(Path(path)) != expected_hash:
            raise ValueError('원본과 다른 파일입니다. 같은 내용의 원본을 선택하세요.')
        with self.connect() as db:
            db.execute('UPDATE assets SET path=?,name=? WHERE id=?', (str(Path(path).resolve()), Path(path).name, identity))
