"""Persistent source candidates, remote image indexes, and independent resumable jobs."""
import json
import time
import uuid
from .providers.wikimedia import qid

ACTIVE=('queued','running','retry')

class OnlineRepository:
    def __init__(self,db,clock=time.time):self.db,self.clock=db,clock

    def reviewed(self):
        with self.db.connect() as conn:
            row=conn.execute("SELECT * FROM provider_reviews WHERE provider='wikimedia'").fetchone()
            return bool(row and row['enabled'] and row['reviewed_at'] and row['evidence'])

    def record_review(self,evidence,enabled=True):
        if not isinstance(evidence,dict) or not evidence.get('documents'):
            raise ValueError('현재 정책 문서 확인 기록이 필요합니다.')
        with self.db.connect() as conn:
            conn.execute("INSERT INTO provider_reviews VALUES('wikimedia',?,?,?) ON CONFLICT(provider) DO UPDATE SET reviewed_at=excluded.reviewed_at,evidence=excluded.evidence,enabled=excluded.enabled",(self.clock(),json.dumps(evidence),int(enabled)))

    def candidate(self,item,reason='외부 식별자·활동 범위 및 소속 확인 필요'):
        identity=qid(item['external_id'])
        if item['kind'] not in ('group','person'):raise ValueError('알 수 없는 아티스트 유형')
        with self.db.connect() as conn:
            existing=conn.execute("SELECT kind,entity_id FROM entity_external_ids WHERE provider='wikidata' AND external_id=?",(identity,)).fetchone()
            status='linked' if existing else 'pending'
            previous=conn.execute('SELECT payload FROM artist_candidates WHERE provider=? AND external_id=?',('wikidata',identity)).fetchone()
            old=json.loads(previous['payload']) if previous else {}
            changed=bool(existing and any(k in old and k in item and old[k]!=item[k] for k in ('name','groups','members','genders')))
            if changed:
                status='pending';reason='외부 이름·소속·인물 정보 변경 제안 · 로컬 수정은 유지됩니다.'
                conn.execute("UPDATE artist_candidates SET status='pending' WHERE provider=? AND external_id=?",('wikidata',identity))
            if existing and existing['kind']!=item['kind']:status='conflict'
            conn.execute("INSERT INTO artist_candidates VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(provider,external_id) DO UPDATE SET name=CASE WHEN artist_candidates.status IN ('pending','conflict') THEN excluded.name ELSE artist_candidates.name END,payload=excluded.payload,updated=excluded.updated,reason=excluded.reason,status=CASE WHEN excluded.status='conflict' THEN 'conflict' ELSE artist_candidates.status END",
                (uuid.uuid4().hex,'wikidata',identity,item['kind'],item['name'],json.dumps(item,ensure_ascii=False),status,reason,self.clock()))

    def linked(self,external_id):
        with self.db.connect() as conn:
            row=conn.execute("SELECT * FROM entity_external_ids WHERE provider='wikidata' AND external_id=?",(qid(external_id),)).fetchone()
            return dict(row) if row else None

    def candidate_item(self,external_id):
        with self.db.connect() as conn:
            row=conn.execute("SELECT * FROM artist_candidates WHERE provider='wikidata' AND external_id=?",(qid(external_id),)).fetchone()
            if not row:raise ValueError('아티스트 후보가 없습니다.')
            return json.loads(row['payload'])

    def candidates(self):
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT id,provider,external_id,kind,name,status,reason,updated FROM artist_candidates WHERE status IN ('pending','conflict') ORDER BY updated DESC LIMIT 300")]

    def accept(self,external_id,local_id=None,gender='unknown',solo=False,group_kind='other'):
        """Explicit review; no name-only merges or overwrite of local corrections."""
        external_id=qid(external_id)
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM artist_candidates WHERE provider=? AND external_id=?',('wikidata',external_id)).fetchone()
            if not row:raise ValueError('후보를 찾을 수 없습니다.')
            payload=json.loads(row['payload'])
            table='groups' if row['kind']=='group' else 'persons'
            link=conn.execute("SELECT * FROM entity_external_ids WHERE provider='wikidata' AND external_id=?",(external_id,)).fetchone()
            if link:
                if link['kind']!=row['kind'] or local_id and link['entity_id']!=local_id:raise ValueError('외부 식별자 연결 충돌입니다.')
                local_id=link['entity_id']
            if local_id:
                if not conn.execute(f'SELECT 1 FROM {table} WHERE id=?',(local_id,)).fetchone():raise ValueError('선택한 로컬 항목이 없습니다.')
                other=conn.execute("SELECT 1 FROM entity_external_ids WHERE provider='wikidata' AND kind=? AND entity_id=? AND external_id<>?",(row['kind'],local_id,external_id)).fetchone()
                if other:raise ValueError('로컬 항목에 다른 외부 식별자가 연결되어 있습니다.')
            else:
                if conn.execute(f'SELECT 1 FROM {table} WHERE name=? COLLATE NOCASE',(row['name'],)).fetchone():raise ValueError('같은 이름이 있습니다. 기존 항목을 직접 선택해 연결하세요.')
                local_id=uuid.uuid4().hex
                aliases=', '.join(payload.get('aliases',[]))
                if row['kind']=='group':conn.execute('INSERT INTO groups VALUES(?,?,?,?)',(local_id,row['name'],group_kind,aliases))
                else:conn.execute('INSERT INTO persons VALUES(?,?,?,?,?)',(local_id,row['name'],aliases,gender,int(solo)))
            conn.execute("INSERT OR IGNORE INTO entity_external_ids VALUES('wikidata',?,?,?)",(external_id,row['kind'],local_id))
            conn.execute("UPDATE artist_candidates SET status='accepted' WHERE id=?",(row['id'],))
            conn.execute('INSERT INTO watch_targets(id,kind,entity_id,external_id,category) VALUES(?,?,?,?,?) ON CONFLICT(kind,entity_id) DO UPDATE SET external_id=excluded.external_id,category=COALESCE(excluded.category,watch_targets.category)',
                (uuid.uuid4().hex,row['kind'],local_id,external_id,payload.get('category')))
            return local_id

    def targets(self):
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT t.*,COALESCE(g.name,p.name,t.external_id) AS name FROM watch_targets t LEFT JOIN groups g ON t.kind='group' AND g.id=t.entity_id LEFT JOIN persons p ON t.kind='person' AND p.id=t.entity_id ORDER BY name")]

    def update_target(self,target_id,metadata):
        """Source proposals stay separate; local names/membership are never overwritten."""
        with self.db.connect() as conn:
            target=conn.execute('SELECT * FROM watch_targets WHERE id=?',(target_id,)).fetchone()
            if not target:raise ValueError('갱신 대상을 찾을 수 없습니다.')
            if qid(metadata['external_id'])!=target['external_id']:raise ValueError('제공처 식별자 불일치')
            conn.execute('UPDATE watch_targets SET category=COALESCE(?,category),last_success=?,next_due=? WHERE id=?',(metadata.get('category'),self.clock(),self.clock()+6*3600,target_id))
            previous=conn.execute('SELECT payload,status FROM artist_candidates WHERE provider=? AND external_id=?',('wikidata',target['external_id'])).fetchone()
            old=json.loads(previous['payload']) if previous else {}
            changed=any(k in old and k in metadata and old[k]!=metadata[k] for k in ('name','groups','members','genders'))
            proposal=old|metadata
            conn.execute('UPDATE artist_candidates SET payload=?,updated=? WHERE provider=? AND external_id=?',(json.dumps(proposal,ensure_ascii=False),self.clock(),'wikidata',target['external_id']))
            if changed:
                conn.execute("UPDATE artist_candidates SET status='pending',name=?,reason=? WHERE provider=? AND external_id=?",(metadata['name'],'외부 이름·소속·인물 정보 변경 제안 · 로컬 수정은 유지됩니다.','wikidata',target['external_id']))

    def index_images(self,target_id,images,cursor):
        with self.db.connect() as conn:
            for image in images:
                existing=conn.execute("SELECT id FROM remote_images WHERE provider='commons' AND source_id=?",(image['source_id'],)).fetchone()
                identity=existing['id'] if existing else uuid.uuid4().hex
                keys=('source_id','source_url','original_url','thumbnail_url','title','width','height','size','mime','source_sha1','license_name','license_url','attribution','restrictions','license_status','captured','published')
                if not existing:
                    conn.execute('INSERT INTO remote_images(id,provider,'+','.join(keys)+',updated,payload) VALUES('+','.join('?' for _ in range(len(keys)+4))+')',(identity,'commons',*(image.get(k) for k in keys),self.clock(),json.dumps(image,ensure_ascii=False)))
                else:
                    conn.execute('UPDATE remote_images SET '+','.join(k+'=?' for k in keys)+',updated=?,payload=? WHERE id=?',(*(image.get(k) for k in keys),self.clock(),json.dumps(image,ensure_ascii=False),identity))
                conn.execute('INSERT OR IGNORE INTO remote_targets VALUES(?,?)',(identity,target_id))
            conn.execute('UPDATE watch_targets SET cursor=?,last_success=?,next_due=? WHERE id=?',(json.dumps(cursor) if cursor else None,self.clock(),self.clock()+6*3600,target_id))

    def images(self,target_id=None,offset=0,limit=200):
        clause=' WHERE EXISTS(SELECT 1 FROM remote_targets r WHERE r.remote_id=i.id AND r.target_id=?)' if target_id else ''
        with self.db.connect() as conn:
            columns='id,provider,source_id,source_url,original_url,thumbnail_url,title,width,height,size,mime,source_sha1,license_name,license_url,attribution,restrictions,license_status,captured,published,thumbnail_path,local_asset_id,updated'
            return [dict(r) for r in conn.execute('SELECT '+','.join('i.'+x for x in columns.split(','))+' FROM remote_images i'+clause+' ORDER BY updated DESC,id LIMIT ? OFFSET ?',((target_id,) if target_id else ())+(limit,offset))]

    def image(self,identity):
        with self.db.connect() as conn:
            row=conn.execute('SELECT * FROM remote_images WHERE id=?',(identity,)).fetchone()
            if not row:raise ValueError('온라인 사진을 찾을 수 없습니다.')
            return dict(row)

    def image_path(self,identity,path,asset_id=None):
        with self.db.connect() as conn:
            if asset_id:conn.execute('UPDATE remote_images SET local_asset_id=? WHERE id=?',(asset_id,identity))
            else:conn.execute('UPDATE remote_images SET thumbnail_path=? WHERE id=?',(str(path),identity))

    def enqueue(self,kind,payload,key=None,force=False,refresh_after=24*3600):
        if kind not in ('discover','enrich','refresh','photos','download'):raise ValueError('지원하지 않는 갱신 작업')
        key=key or kind+':'+str(payload.get('target_id',payload.get('remote_id','all')))
        now=self.clock()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM sync_jobs WHERE job_key=?',(key,)).fetchone()
            if row:
                if row['state'] in ACTIVE:return row['id']
                if not force and (row['state']!='done' or now-row['updated']<refresh_after):return row['id']
                conn.execute("UPDATE sync_jobs SET state='queued',payload=?,attempts=0,next_run=?,updated=?,message='',result=NULL WHERE id=?",(json.dumps(payload),now,now,row['id']))
                return row['id']
            identity=uuid.uuid4().hex
            conn.execute('INSERT INTO sync_jobs(id,job_key,kind,payload,next_run,updated) VALUES(?,?,?,?,?,?)',(identity,key,kind,json.dumps(payload),now,now))
            return identity

    def claim(self):
        now=self.clock()
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT * FROM sync_jobs WHERE state IN ('queued','retry') AND next_run<=? ORDER BY CASE kind WHEN 'download' THEN 0 WHEN 'refresh' THEN 1 WHEN 'photos' THEN 2 WHEN 'enrich' THEN 3 ELSE 4 END,updated LIMIT 1",(now,)).fetchone()
            if not row:return None
            conn.execute("UPDATE sync_jobs SET state='running',attempts=attempts+1,updated=? WHERE id=?",(now,row['id']))
            result=dict(row);result['attempts']+=1;result['payload']=json.loads(result['payload'])
            return result

    def finish(self,identity,state,message='',result=None,payload=None,delay=0):
        with self.db.connect() as conn:
            conn.execute('UPDATE sync_jobs SET state=?,message=?,result=?,payload=COALESCE(?,payload),next_run=?,updated=?,attempts=CASE WHEN ? THEN 0 ELSE attempts END WHERE id=?',(state,message,json.dumps(result) if result is not None else None,json.dumps(payload) if payload is not None else None,self.clock()+delay,self.clock(),int(state=='queued' and result is not None),identity))

    def jobs(self):
        with self.db.connect() as conn:return [dict(r) for r in conn.execute('SELECT * FROM sync_jobs ORDER BY updated DESC LIMIT 200')]

    def retry(self,identity):
        with self.db.connect() as conn:
            conn.execute("UPDATE sync_jobs SET state='queued',attempts=0,next_run=?,message='' WHERE id=? AND state IN ('failed','blocked','cancelled','retry')",(self.clock(),identity))

    def recover(self):
        with self.db.connect() as conn:conn.execute("UPDATE sync_jobs SET state='retry',next_run=?,message='중단된 작업을 재개합니다.' WHERE state='running'",(self.clock(),))

    def schedule(self):
        self.enqueue('discover',{})
        for target in self.targets():
            if target['active'] and target['next_due']<=self.clock():
                self.enqueue('refresh',{'target_id':target['id']},refresh_after=6*3600)
                if target['category']:
                    self.enqueue('photos',{'target_id':target['id'],'cursor':json.loads(target['cursor']) if target['cursor'] else None},refresh_after=6*3600)

    def source_for_asset(self,asset_id):
        with self.db.connect() as conn:return [dict(r) for r in conn.execute('SELECT source_url,license_name,license_url,attribution,restrictions FROM remote_images WHERE local_asset_id=?',(asset_id,))]
