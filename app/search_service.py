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
        self.source_status=[]
        if providers is None and online.settings.values.get('automatic_search',True) is True:
            from .automatic_sources import configured
            providers,self.source_status=configured(online)
        if providers is None:
            from .providers.page_search import PageSearchProvider
            providers=[]
            if online.settings.values.get('naver_search_enabled',False):
                from .providers.naver_images import NaverImageProvider
                providers.append(NaverImageProvider(online))
            if online.settings.values.get('dbkpop_search_enabled',False):
                from .providers.dbkpop import DBKpopProvider
                providers.append(DBKpopProvider(online))
            if online.settings.values.get('flickr_feed_enabled',False):
                from .providers.flickr_feed import FlickrFeedProvider
                providers.append(FlickrFeedProvider(online))
            if online.settings.values.get('kpopping_browser_enabled',False):
                from .providers.kpopping_browser import KpoppingBrowserProvider
                providers.append(KpoppingBrowserProvider(online))
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
    def provider_query(self,provider,variant,session):
        if not getattr(provider,'identity_search',False):return variant
        # Entity pickers accept an exact name, not free-text composition/color
        # hints. The same filters still apply to the resulting gallery.
        filters=json.loads(session['filters'])
        suffix=COMPOSITION_TERMS.get(filters.get('composition'),'')
        if filters.get('color'):suffix+=' '+filters['color']
        suffix=suffix.strip()
        ending=' '+suffix
        return variant[:-len(ending)] if suffix and variant.endswith(ending) else variant
    def page(self,identity,cancelled,on_update=None,on_progress=None):
        session=self.session(identity)
        if cancelled.is_set():raise Cancelled()
        if session['state']=='exhausted':return dict(added=0,state='exhausted')
        if session['state']=='blocked':return dict(added=0,state='blocked',error=session['message'])
        if len(self.providers)>1:return self._parallel_page(identity,session,cancelled,on_update,on_progress)
        plans=[];index=session["variant"]
        try:
            if not self.providers:raise RemoteError("Wikimedia는 검색에서 제외됐습니다. 다른 검색 출처의 실제 접근·이용 조건 확인이 필요합니다. 현재 검증된 검색 출처가 없습니다.",blocked=True)
            variants=json.loads(session['variants']);index=session['variant'];cursor=json.loads(session['cursor']) if session['cursor'] else None
            plans=[(provider,variant) for variant in variants for provider in self.providers.values()]
            provider,query=plans[index]
            if on_progress:on_progress(dict(source=provider.name,state='queued'))
            entries,next_cursor=self._request(provider,self.provider_query(provider,query,session),cursor,cancelled,on_progress)
            if cancelled.is_set():raise Cancelled()
            if next_cursor==cursor and next_cursor is not None:raise RemoteError('제공처가 같은 커서를 반복해 추가 검색을 중단했습니다.')
            with self.db.connect() as c:
                position=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM search_hits WHERE session_id=?',(identity,)).fetchone()[0];added=0
                for entry in entries:
                    inserted=c.execute('INSERT OR IGNORE INTO search_hits VALUES(?,?,?)',(identity,entry,position)).rowcount
                    if inserted:added+=1;position+=1
                if next_cursor is None:index+=1
                state='exhausted' if index>=len(plans) else 'ready'
                c.execute('UPDATE search_sessions SET variant=?,cursor=?,state=?,message=?,updated=? WHERE id=?',(index,json.dumps(next_cursor) if next_cursor is not None else None,state,'',time.time(),identity))
            if on_progress:on_progress(dict(source=provider.name,state='complete',added=added))
            return dict(added=added,state=state)
        except Cancelled:raise
        except Exception as error:
            if on_progress and plans:on_progress(dict(source=plans[index][0].name,state='failed',error=str(error)))
            if plans and index+1<len(plans) and not getattr(error,'blocked',False):
                with self.db.connect() as c:c.execute("UPDATE search_sessions SET variant=?,cursor=NULL,state='ready',message=?,updated=? WHERE id=?",(index+1,str(error),time.time(),identity))
                return dict(added=0,state="ready",error=str(error))
            with self.db.connect() as c:c.execute('UPDATE search_sessions SET state=?,message=?,updated=? WHERE id=?',('blocked' if getattr(error,'blocked',False) else 'error',str(error),time.time(),identity))
            raise

    def _request(self,provider,query,cursor,cancelled,on_progress):
        if cancelled.is_set():raise Cancelled()
        if on_progress:on_progress(dict(source=provider.name,state='running'))
        return provider.search_images(query,cursor,cancelled)

    def _parallel_page(self,identity,session,cancelled,on_update,on_progress=None):
        from concurrent.futures import ThreadPoolExecutor,as_completed
        variants=json.loads(session['variants']);index=session['variant']
        if index>=len(variants):return dict(added=0,state='exhausted')
        saved=json.loads(session['cursor']) if session['cursor'] else {}
        states=saved.get('providers',{});disabled=set(saved.get('disabled',[]))
        names=sorted((name for name in self.providers if name not in disabled and states.get(name,{}).get('state','ready')=='ready'),key=lambda name:states.get(name,{}).get('pages',0))
        added=0;errors=[]
        if on_progress:
            for name in names:on_progress(dict(source=name,state='queued'))
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='image-search') as executor:
            futures={executor.submit(self._request,self.providers[name],self.provider_query(self.providers[name],variants[index],session),states.get(name,{}).get('cursor'),cancelled,on_progress):name for name in names}
            for future in as_completed(futures):
                name=futures[future]
                if cancelled.is_set():raise Cancelled()
                try:
                    entries,cursor=future.result()
                    previous=states.get(name,{}).get('cursor')
                    if cursor is not None and cursor==previous:raise RemoteError('제공처 커서가 반복됩니다.')
                    with self.db.connect() as c:
                        c.execute('BEGIN IMMEDIATE')
                        batch_added=0
                        position=c.execute('SELECT COALESCE(MAX(position),-1)+1 FROM search_hits WHERE session_id=?',(identity,)).fetchone()[0]
                        for entry in entries:
                            if cancelled.is_set():raise Cancelled()
                            inserted=c.execute('INSERT OR IGNORE INTO search_hits VALUES(?,?,?)',(identity,entry,position)).rowcount
                            if inserted:batch_added+=1;position+=1
                        states[name]=dict(state='ready' if cursor is not None else 'done',cursor=cursor,pages=states.get(name,{}).get('pages',0)+1)
                    added+=batch_added
                    if on_progress:on_progress(dict(source=name,state='complete',added=batch_added))
                    if on_update:on_update(identity)
                except Cancelled:raise
                except Exception as error:
                    if on_progress:on_progress(dict(source=name,state='failed',error=str(error)))
                    states[name]=dict(state='failed',cursor=None,error=str(error));errors.append(name+': '+str(error))
                    if getattr(error,'blocked',False):disabled.add(name)
        remaining=any(name not in disabled and states.get(name,{}).get('state','ready')=='ready' for name in self.providers)
        if not remaining:index+=1;states={}
        state='blocked' if len(disabled)==len(self.providers) else ('exhausted' if index>=len(variants) else 'ready')
        stored=dict(providers=states,disabled=sorted(disabled))
        with self.db.connect() as c:c.execute('UPDATE search_sessions SET variant=?,cursor=?,state=?,message=?,updated=? WHERE id=?',(index,json.dumps(stored),state,'; '.join(errors),time.time(),identity))
        result=dict(added=added,state=state)
        if errors:result['error']='; '.join(errors)
        return result
