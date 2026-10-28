"""Search integration using reviewed provider adapters and existing image IDs.

No inferred artist identities, blanket permissions or simulated online results.
"""
import json,time,uuid
from .providers.http import Cancelled,RemoteError

COMPOSITION_TERMS={None:'','face_extreme':'face close up','face_close':'portrait close up','face_shoulders':'headshot','upper_body':'upper body','waist_up':'waist up','knees_up':'three quarter portrait','full_body':'full body full length'}


class SearchService:
    def __init__(self,online,providers=None):
        self.online=online;self.db=online.db
        self.configuration_errors=[]
        if providers is None:
            from .providers.page_search import PageSearchProvider
            providers=[]
            for item in online.settings.values.get("image_search_templates",[])[:8]:
                try:
                    if item.get("source")=="wikimedia":continue
                    providers.append(PageSearchProvider(online.web,item["source"],item["template"]))
                except (ValueError,KeyError,AttributeError) as error:self.configuration_errors.append(str(error))
        self.providers={p.name:p for p in providers if p.name!="wikimedia"}
        # Additive feature tables, created transactionally; existing library unchanged.
        with self.db.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS search_sessions(id TEXT PRIMARY KEY,query TEXT NOT NULL,filters TEXT NOT NULL,variants TEXT NOT NULL,variant INTEGER NOT NULL DEFAULT 0,cursor TEXT,state TEXT NOT NULL DEFAULT "ready",message TEXT NOT NULL DEFAULT "",updated REAL NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS search_hits(session_id TEXT NOT NULL REFERENCES search_sessions(id) ON DELETE CASCADE,entry_id TEXT NOT NULL REFERENCES gallery_items(id) ON DELETE CASCADE,position INTEGER NOT NULL,PRIMARY KEY(session_id,entry_id))')
            c.execute('CREATE INDEX IF NOT EXISTS search_hit_order ON search_hits(session_id,position)')
    def variants(self,query,filters):
        query=query.strip()
        if not 1<=len(query)<=120 or any(ord(ch)<32 for ch in query):raise ValueError('검색어는 1~120자로 입력하세요.')
        names=[query]
        # Only explicitly stored aliases are expanded; no guessed identity or membership.
        with self.db.connect() as c:
            for table in ('persons','groups'):
                for row in c.execute(f'SELECT name,aliases FROM {table} WHERE name=? COLLATE NOCASE',(query,)):
                    names += [row['name']]+[x.strip() for x in row['aliases'].replace('\n',',').split(',') if x.strip()]
        suffix=COMPOSITION_TERMS.get(filters.composition,'')
        if filters.color:suffix+=' '+filters.color # discovery hint only; never a verified color match
        return list(dict.fromkeys((name+' '+suffix).strip()[:200] for name in names))[:8]
    def start(self,query,filters):
        variants=self.variants(query,filters);identity=uuid.uuid4().hex
        with self.db.connect() as c:c.execute('INSERT INTO search_sessions(id,query,filters,variants,updated) VALUES(?,?,?,?,?)',(identity,query.strip(),json.dumps(filters.dictionary()),json.dumps(variants),time.time()))
        return identity
    def session(self,identity):
        with self.db.connect() as c:
            row=c.execute('SELECT * FROM search_sessions WHERE id=?',(identity,)).fetchone()
            if row is None:raise ValueError('검색 세션이 없습니다.')
            return dict(row)
    def page(self,identity,cancelled):
        session=self.session(identity)
        if cancelled.is_set():raise Cancelled()
        if session['state']=='exhausted':return dict(added=0,state='exhausted')
        plans=[];index=session["variant"]
        try:
            if not self.providers:raise RemoteError("Wikimedia는 검색에서 제외됐습니다. 다른 검색 출처의 실제 접근·이용 조건 확인이 필요합니다. 현재 검증된 검색 출처가 없습니다.",blocked=True)
            variants=json.loads(session['variants']);index=session['variant'];cursor=json.loads(session['cursor']) if session['cursor'] else None
            plans=[(provider,variant) for variant in variants for provider in self.providers.values()]
            provider,query=plans[index]
            entries,next_cursor=provider.search_images(query,cursor,cancelled)
            if cancelled.is_set():raise Cancelled()
            if next_cursor==cursor and next_cursor is not None:raise RemoteError('제공처가 같은 커서를 반복해 추가 검색을 중단했습니다.')
            with self.db.connect() as c:
                position=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM search_hits WHERE session_id=?',(identity,)).fetchone()[0];added=0
                for entry in entries:
                    inserted=c.execute('INSERT OR IGNORE INTO search_hits VALUES(?,?,?)',(identity,entry,position)).rowcount
                    if inserted:added+=1;position+=1
                if next_cursor is None:index+=1
                state='exhausted' if index>=len(plans) else 'ready'
                c.execute('UPDATE search_sessions SET variant=?,cursor=?,state=?,message=?,updated=? WHERE id=?',(index,json.dumps(next_cursor) if next_cursor else None,state,'',time.time(),identity))
            return dict(added=added,state=state)
        except Cancelled:raise
        except Exception as error:
            if plans and index+1<len(plans):
                with self.db.connect() as c:c.execute("UPDATE search_sessions SET variant=?,cursor=NULL,state='ready',message=?,updated=? WHERE id=?",(index+1,str(error),time.time(),identity))
                return dict(added=0,state="ready",error=str(error))
            with self.db.connect() as c:c.execute('UPDATE search_sessions SET state=?,message=?,updated=? WHERE id=?',('blocked' if getattr(error,'blocked',False) else 'error',str(error),time.time(),identity))
            raise
