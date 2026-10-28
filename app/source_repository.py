import json
import time
import uuid
from .source_specs import SourceSpec,CATALOG,OPERATIONS,STATES,OPERATION_LABELS
from .providers.http import RemoteError

class SourceRepository:
    def __init__(self,db,clock=time.time):
        self.db,self.clock=db,clock
        with db.connect() as conn:
            for spec in CATALOG:
                conn.execute('INSERT OR IGNORE INTO source_profiles VALUES(?,?,?,?,?,?,?,?)',(spec.id,spec.name,spec.category,spec.adapter,json.dumps(spec.dictionary()),spec.digest,int(spec.adapter=='local'),clock()))

    def save_spec(self,data):
        spec=SourceSpec.load(data)
        built_in=next((item for item in CATALOG if item.id==spec.id),None)
        if built_in and (spec.adapter!=built_in.adapter or spec.domains!=built_in.domains):raise ValueError('기본 제공 API/후보 출처의 어댑터와 도메인은 바꿀 수 없습니다. 새 사용자 출처 ID로 등록하세요.')
        with self.db.connect() as conn:
            # A changed manifest automatically invalidates permissions tied to its old digest.
            conn.execute('INSERT INTO source_profiles VALUES(?,?,?,?,?,?,0,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,category=excluded.category,adapter=excluded.adapter,spec=excluded.spec,enabled=CASE WHEN source_profiles.digest<>excluded.digest THEN 0 ELSE source_profiles.enabled END,digest=excluded.digest,updated=excluded.updated',
                (spec.id,spec.name,spec.category,spec.adapter,json.dumps(spec.dictionary(),ensure_ascii=False),spec.digest,self.clock()))
        return spec

    def spec(self,identity):
        with self.db.connect() as conn:
            row=conn.execute('SELECT spec FROM source_profiles WHERE id=?',(identity,)).fetchone()
            if not row:raise ValueError('출처 설정이 없습니다.')
            return SourceSpec.load(json.loads(row['spec']))

    def profiles(self):
        with self.db.connect() as conn:return [dict(r) for r in conn.execute('SELECT id,name,category,adapter,enabled FROM source_profiles ORDER BY category,name')]

    def enable(self,identity,enabled):
        spec=self.spec(identity)
        if enabled and spec.adapter=='candidate':raise ValueError('이 출처는 검토 후보이며 실행 어댑터가 없습니다.')
        with self.db.connect() as conn:conn.execute('UPDATE source_profiles SET enabled=? WHERE id=?',(int(enabled),identity))

    def grant(self,identity,operation,state,evidence,resource=None):
        if operation not in OPERATIONS or state not in STATES:raise ValueError('알 수 없는 권한 항목/상태입니다.')
        spec=self.spec(identity)
        if state=='allowed' and (not isinstance(evidence,dict) or not evidence.get('reference') or not evidence.get('note')):
            raise ValueError('허용 근거 URL/문서 식별자와 검토 내용을 기록하세요.')
        evidence=json.dumps(evidence,ensure_ascii=False)
        with self.db.connect() as conn:
            if resource:
                conn.execute('INSERT INTO resource_permissions(resource_id,operation,state,evidence,checked) VALUES(?,?,?,?,?) ON CONFLICT(resource_id,operation) DO UPDATE SET state=excluded.state,evidence=excluded.evidence,checked=excluded.checked,managed_by="manual"',(resource,operation,state,evidence,self.clock()))
            else:
                conn.execute('INSERT INTO source_permissions VALUES(?,?,?,?,?,?) ON CONFLICT(source_id,operation) DO UPDATE SET state=excluded.state,evidence=excluded.evidence,profile_digest=excluded.profile_digest,checked=excluded.checked',(identity,operation,state,evidence,spec.digest,self.clock()))

    def grant_many(self,identity,values,evidence,resource=None,enabled=None):
        spec=self.spec(identity)
        for operation,state in values.items():
            if operation not in OPERATIONS or state not in STATES:raise ValueError('알 수 없는 권한 항목/상태입니다.')
            if state=='allowed' and (not isinstance(evidence,dict) or not evidence.get('reference') or not evidence.get('note')):raise ValueError('허용 근거와 검토 내용을 기록하세요.')
        if enabled and spec.adapter=='candidate':raise ValueError('이 출처는 검토 후보이며 실행 어댑터가 없습니다.')
        encoded=json.dumps(evidence,ensure_ascii=False)
        with self.db.connect() as conn:
            for operation,state in values.items():
                if resource:conn.execute('INSERT INTO resource_permissions(resource_id,operation,state,evidence,checked) VALUES(?,?,?,?,?) ON CONFLICT(resource_id,operation) DO UPDATE SET state=excluded.state,evidence=excluded.evidence,checked=excluded.checked,managed_by="manual"',(resource,operation,state,encoded,self.clock()))
                else:conn.execute('INSERT INTO source_permissions VALUES(?,?,?,?,?,?) ON CONFLICT(source_id,operation) DO UPDATE SET state=excluded.state,evidence=excluded.evidence,profile_digest=excluded.profile_digest,checked=excluded.checked',(identity,operation,state,encoded,spec.digest,self.clock()))
            if enabled is not None:conn.execute('UPDATE source_profiles SET enabled=? WHERE id=?',(int(enabled),identity))

    def proposals(self,identity):
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT m.kind,m.entity_id,COALESCE(p.name,g.name,m.entity_id) AS name,m.status FROM web_match_proposals m LEFT JOIN persons p ON m.kind='person' AND p.id=m.entity_id LEFT JOIN groups g ON m.kind='group' AND g.id=m.entity_id WHERE m.item_id=? LIMIT 50",(identity,))]

    def permissions(self,identity,resource=None):
        spec=self.spec(identity)
        with self.db.connect() as conn:
            if resource:rows=conn.execute('SELECT operation,state,evidence FROM resource_permissions WHERE resource_id=?',(resource,)).fetchall()
            else:rows=conn.execute('SELECT operation,state,evidence FROM source_permissions WHERE source_id=? AND profile_digest=?',(identity,spec.digest)).fetchall()
        result={op:dict(state='review',evidence={}) for op in OPERATIONS}
        result.update({row['operation']:dict(state=row['state'],evidence=json.loads(row['evidence'])) for row in rows})
        return result

    def allowed_in(self,conn,identity,operation,resource=None):
        if operation not in OPERATIONS:raise ValueError('알 수 없는 권한 항목입니다.')
        resource_clause=" AND EXISTS(SELECT 1 FROM resource_permissions r WHERE r.resource_id=? AND r.operation=s.operation AND r.state='allowed')" if resource is not None else ''
        arguments=(identity,operation,resource) if resource is not None else (identity,operation)
        return conn.execute("SELECT 1 FROM source_profiles p JOIN source_permissions s ON s.source_id=p.id AND s.profile_digest=p.digest WHERE p.id=? AND p.enabled=1 AND p.adapter<>'candidate' AND s.operation=? AND s.state='allowed'"+resource_clause,arguments).fetchone() is not None

    def allowed(self,identity,operation,resource=None):
        with self.db.connect() as conn:return self.allowed_in(conn,identity,operation,resource)

    def require(self,identity,operation,resource=None):
        if not self.allowed(identity,operation,resource):
            raise RemoteError('이 출처/사진의 해당 이용 권한이 확인되지 않았거나 제한되어 있습니다: '+OPERATION_LABELS.get(operation,operation),blocked=True)

    def add_page(self,source_id,url,mode='html'):
        from .page_discovery import clean_url
        spec=self.spec(source_id)
        url=clean_url(url,url)
        if not url:raise ValueError('공개 HTTPS URL을 확인하세요.')
        from urllib.parse import urlsplit
        if urlsplit(url).hostname not in spec.domains:raise ValueError('출처에 등록된 정확한 도메인만 사용할 수 있습니다.')
        if mode not in ('html','rendered','direct'):raise ValueError('지원하지 않는 탐색 방식입니다.')
        with self.db.connect() as conn:
            row=conn.execute('SELECT id FROM source_pages WHERE source_id=? AND url=?',(source_id,url)).fetchone()
            if row:return row['id']
            identity=uuid.uuid4().hex
            conn.execute('INSERT INTO source_pages(id,source_id,url,mode) VALUES(?,?,?,?)',(identity,source_id,url,mode))
            return identity

    def pages(self,source_id=None):
        with self.db.connect() as conn:
            clause=' WHERE source_id=?' if source_id else ''
            return [dict(r) for r in conn.execute('SELECT * FROM source_pages'+clause+' ORDER BY next_due LIMIT 300',(source_id,) if source_id else ())]

    def page(self,identity):
        with self.db.connect() as conn:
            row=conn.execute('SELECT * FROM source_pages WHERE id=?',(identity,)).fetchone()
            if not row:raise ValueError('등록된 페이지가 없습니다.')
            return dict(row)

    def index_page(self,page,parsed):
        with self.db.connect() as conn:
            conn.execute("UPDATE source_pages SET title=?,last_success=?,next_due=?,status='indexed',published=?,metadata=?,message=? WHERE id=?",(parsed.title,self.clock(),self.clock()+6*3600,parsed.published,json.dumps(parsed.metadata,ensure_ascii=False),'이미지 후보 없음 · 구조 변경/렌더링 필요 여부 확인' if not parsed.candidates else f'후보 {len(parsed.candidates)}개 · 해상도/권한 별도 확인',page['id']))
            for item in parsed.candidates:
                row=conn.execute('SELECT id FROM web_images WHERE page_id=? AND url=?',(page['id'],item.url)).fetchone()
                identity=row['id'] if row else uuid.uuid4().hex
                conn.execute('INSERT INTO web_images(id,page_id,source_id,url,logical_key,title,role,declared_width,declared_height,provenance,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(page_id,url) DO UPDATE SET title=excluded.title,role=excluded.role,declared_width=excluded.declared_width,declared_height=excluded.declared_height,provenance=excluded.provenance,updated=excluded.updated',
                    (identity,page['id'],page['source_id'],item.url,item.logical_key,item.title,item.role,item.declared_width,item.declared_height,json.dumps(item.provenance,ensure_ascii=False),self.clock()))
                conn.execute('INSERT INTO resource_permissions(resource_id,operation,state,evidence,checked) VALUES(?,?,?,?,?) ON CONFLICT(resource_id,operation) DO NOTHING',('web:'+identity,'metadata_search','allowed',json.dumps({'reference':page['url'],'note':'Permitted page metadata indexing only; no binary/AI permission implied.'}),self.clock()))

    def images_for_urls(self,page_id,urls):
        """Return this discovery batch in order, without older page candidates."""
        urls=list(dict.fromkeys(urls));found={}
        with self.db.connect() as conn:
            for offset in range(0,len(urls),400):
                batch=urls[offset:offset+400]
                placeholders=','.join('?' for _ in batch)
                rows=conn.execute('SELECT w.*,p.url AS page_url,p.title AS page_title,p.published AS page_published,p.last_success AS page_checked FROM web_images w JOIN source_pages p ON p.id=w.page_id WHERE w.page_id=? AND w.url IN ('+placeholders+')',(page_id,*batch))
                found.update((row['url'],dict(row)) for row in rows)
        return [found[url] for url in urls if url in found]

    def images(self,source_id=None,page_id=None,offset=0,limit=200):
        where,args=[],[]
        if source_id:where.append('w.source_id=?');args.append(source_id)
        if page_id:where.append('w.page_id=?');args.append(page_id)
        clause=' WHERE '+' AND '.join(where) if where else ''
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute('SELECT w.*,p.url AS page_url,p.title AS page_title,p.published AS page_published,p.last_success AS page_checked FROM web_images w JOIN source_pages p ON p.id=w.page_id'+clause+' ORDER BY w.updated DESC,w.id LIMIT ? OFFSET ?',(*args,limit,offset))]

    def image(self,identity):
        with self.db.connect() as conn:
            row=conn.execute('SELECT w.*,p.url AS page_url,p.published AS page_published,p.last_success AS page_checked FROM web_images w JOIN source_pages p ON p.id=w.page_id WHERE w.id=?',(identity,)).fetchone()
            if not row:raise ValueError('이미지 후보를 찾을 수 없습니다.')
            return dict(row)

    def measured(self,identity,meta,path,dhash=None):
        with self.db.connect() as conn:
            old=conn.execute('SELECT sha256 FROM web_images WHERE id=?',(identity,)).fetchone()
            changed=bool(old['sha256'] and old['sha256']!=meta['sha256'])
            if changed:
                conn.execute('DELETE FROM perceptual_features WHERE item_id=?',(identity,))
                conn.execute("UPDATE resource_permissions SET state='review',evidence=? WHERE resource_id=? AND operation<>'metadata_search'",(json.dumps({'note':'File content changed; review the new revision.'}),'web:'+identity))
                conn.execute('UPDATE web_images SET local_asset_id=NULL,dhash=NULL WHERE id=?',(identity,))
            conn.execute('UPDATE web_images SET width=?,height=?,size=?,mime=?,sha256=?,cache_path=?,dhash=COALESCE(?,dhash),updated=? WHERE id=?',(meta['width'],meta['height'],meta['size'],meta.get('mime',meta['format']),meta['sha256'],str(path),dhash,self.clock(),identity))
            rights=[dict(r) for r in conn.execute('SELECT operation,state,evidence,checked FROM resource_permissions WHERE resource_id=?',('web:'+identity,))]
            conn.execute('INSERT OR IGNORE INTO web_revisions(item_id,sha256,path,width,height,mime,size,dhash,created,evidence) VALUES(?,?,?,?,?,?,?,?,?,?)',(identity,meta['sha256'],str(path),meta['width'],meta['height'],meta.get('mime',meta['format']),meta['size'],dhash,self.clock(),json.dumps(rights,ensure_ascii=False)))
            matches=conn.execute('SELECT id FROM web_images WHERE sha256=? AND id<>?',(meta['sha256'],identity)).fetchall()
            for match in matches:
                left,right=sorted((identity,match['id']))
                conn.execute('INSERT OR IGNORE INTO image_relations VALUES(?,?,?,?,?)',(left,right,'exact_bytes',1.0,json.dumps({'sha256':meta['sha256']})))
            return changed

    def attach(self,item,asset_id):
        with self.db.connect() as conn:
            conn.execute('UPDATE web_images SET local_asset_id=? WHERE id=?',(asset_id,item['id']))
            conn.execute('UPDATE web_revisions SET asset_id=? WHERE item_id=? AND sha256=?',(asset_id,item['id'],item['sha256']))
            rights=conn.execute("SELECT evidence FROM resource_permissions WHERE resource_id=? AND operation='original_download'",('web:'+item['id'],)).fetchone()
            evidence=json.loads(rights['evidence']) if rights else {}
            conn.execute('INSERT OR IGNORE INTO asset_sources VALUES(?,?,?,?,?,?,?,?)',(uuid.uuid4().hex,asset_id,item['source_id'],item['id']+'@'+item['sha256'],item['page_url'],item['url'],evidence.get('license_url',''),evidence.get('attribution','')))

    def matches(self,identity):
        with self.db.connect() as conn:return [dict(r) for r in conn.execute('SELECT * FROM image_relations WHERE left_id=? OR right_id=? LIMIT 100',(identity,identity))]
