"""Independent reviewed page discovery, measured versions and verified local storage."""
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid
import re
import unicodedata
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from PIL import Image,ImageOps
from .source_repository import SourceRepository
from .providers.http import HttpClient,RemoteError,Cancelled,validate_url
from .page_discovery import discover_html,ParsedPage,ImageCandidate,choose_version,clean_url
from .images import inspect_image,file_hash,decode_preview

def decode_html_bytes(raw,headers):
    content_type=next((str(v) for k,v in headers.items() if k.lower()=='content-type'),'')
    match=re.search(r'charset\s*=\s*["\']?([a-zA-Z0-9_-]{1,32})',content_type,re.I)
    if not match:match=re.search(r'charset\s*=\s*["\']?([a-zA-Z0-9_-]{1,32})',raw[:4096].decode('ascii','ignore'),re.I)
    encoding=match.group(1).lower() if match else 'utf-8-sig'
    permitted={'utf-8','utf8','utf-8-sig','euc-kr','euc_kr','cp949','ks_c_5601-1987','windows-1252','iso-8859-1','shift_jis','gb18030'}
    if encoding not in permitted:encoding='utf-8-sig'
    return raw.decode(encoding,'replace')

class WebService:
    def __init__(self,db,settings,clock=time.time,http_factory=None,renderer=None):
        self.db,self.settings,self.clock=db,settings,clock
        self.repo=SourceRepository(db,clock)
        self.clients={}
        self.http_factory=http_factory or (lambda spec:HttpClient(interval=spec.interval_seconds,allowed_hosts=spec.domains,public_only=True))
        self.renderer=renderer

    def client(self,source):
        spec=self.repo.spec(source)
        key=(source,spec.digest)
        if key not in self.clients:
            self.clients={k:v for k,v in self.clients.items() if k[0]!=source}
            self.clients[key]=self.http_factory(spec)
        return self.clients[key]

    def robots(self,source,url,cancelled):
        spec=self.repo.spec(source);host=urlsplit(url).hostname
        validate_url(url,spec.domains)
        with self.db.connect() as conn:
            row=conn.execute('SELECT * FROM source_robots WHERE source_id=? AND host=?',(source,host)).fetchone()
        if row and self.clock()-row['checked']<3600:body=row['body']
        else:
            try:
                with self.client(source).open('https://'+host+'/robots.txt',cancelled,accept='text/plain') as response:
                    data=response.read(256*1024+1)
                    if len(data)>256*1024:raise RemoteError('robots 응답 크기 한도를 초과했습니다.')
                    body=data.decode('utf-8','replace')
                    if body.lstrip().startswith('<'):raise RemoteError('robots 대신 HTML/접근 확인 페이지가 반환되었습니다.',blocked=True)
            except RemoteError as error:
                if error.status not in (404,410):raise
                body='User-agent: *\nAllow: /'
            with self.db.connect() as conn:conn.execute('INSERT INTO source_robots VALUES(?,?,?,?) ON CONFLICT(source_id,host) DO UPDATE SET body=excluded.body,checked=excluded.checked',(source,host,body,self.clock()))
        rules=RobotFileParser();rules.parse(body.splitlines())
        if not rules.can_fetch('K-IDOL-ARCHIVE',url):raise RemoteError('robots 정책에서 이 경로의 자동 접근을 허용하지 않습니다.',blocked=True)

    def read(self,source,url,cancelled,limit,accept):
        self.robots(source,url,cancelled)
        with self.client(source).open(url,cancelled,accept=accept) as response:
            headers=dict(response.headers)
            content_type=next((str(v).lower() for k,v in headers.items() if k.lower()=='content-type'),'')
            if not content_type or not any(kind in content_type for kind in ('text/','javascript','json','xhtml')):
                raise RemoteError('페이지 탐색은 텍스트 리소스만 읽습니다. 이미지 다운로드 권한은 별도로 필요합니다.',blocked=True)
            raw=response.read(limit+1)
        if len(raw)>limit:raise RemoteError('응답 크기 한도를 초과했습니다.')
        if cancelled.is_set():raise Cancelled()
        return raw,headers

    def scan(self,page_id,cancelled):
        page=self.repo.page(page_id);source=page['source_id'];spec=self.repo.spec(source)
        self.repo.require(source,'metadata_search')
        if json.loads(page['metadata'] or '{}').get('search_managed'):
            raise RemoteError('검색 전용 출처입니다. 인터넷 검색에서 해당 검색 경로로 다시 요청하세요.',blocked=True)
        if spec.adapter not in ('html','rendered','direct'):raise RemoteError('이 출처의 공개 페이지 어댑터는 아직 지원하지 않습니다.',blocked=True)
        if spec.adapter=='direct' and page['mode']!='direct':raise RemoteError('직접 이미지 출처에는 직접 URL 모드를 사용하세요.',blocked=True)
        if page['mode']=='direct':
            parsed=ParsedPage('',[ImageCandidate(page['url'],'direct',role='provided_version')])
        else:
            raw,headers=self.read(source,page['url'],cancelled,2*1024*1024,'text/html')
            html=decode_html_bytes(raw,headers)
            # Reject password/CAPTCHA pages before any script execution.
            parsed=discover_html(html,page['url'],spec.original_attributes)
            if page['mode']=='rendered':
                if spec.adapter!='rendered':raise RemoteError('이 출처에는 브라우저 렌더링이 등록되지 않았습니다.',blocked=True)
                self.repo.require(source,'browser_render')
                if self.renderer is None:
                    from .rendered_page import render_page
                    renderer=render_page
                else:renderer=self.renderer
                def fetch(url):
                    self.repo.require(source,'metadata_search');self.repo.require(source,'browser_render')
                    return self.read(source,url,cancelled,2*1024*1024,'text/javascript,text/css,application/json')
                html=renderer(html,page['url'],spec,fetch,cancelled)
                parsed=discover_html(html,page['url'],spec.original_attributes)
        self.repo.require(source,'metadata_search')
        self.repo.index_page(page,parsed)
        self.match_text(page_id)
        return {'indexed':len(parsed.candidates),'complete':True}

    def match_text(self,page_id):
        """Names in metadata produce proposals only, never ImagePerson bindings."""
        aliases={}
        with self.db.connect() as conn:
            for kind,table in (('person','persons'),('group','groups')):
                for row in conn.execute(f'SELECT id,name,aliases FROM {table} LIMIT 5000'):
                    for name in [row['name'],*row['aliases'].split(',')]:
                        name=unicodedata.normalize('NFKC',name).strip().casefold()
                        if 2<=len(name)<=100:aliases.setdefault(name,[]).append((kind,row['id']))
        if not aliases:return
        selected=sorted(aliases,key=lambda x:(-len(x),x))[:3000]
        pattern=re.compile(r'(?<!\w)('+'|'.join(re.escape(x) for x in selected)+r')(?!\w)')
        page=self.repo.page(page_id)
        with self.db.connect() as conn:
            for item in self.repo.images(page_id=page_id,limit=500):
                text=unicodedata.normalize('NFKC',page['title']+' '+item['title']).casefold()
                for match in pattern.finditer(text):
                    for kind,entity in aliases[match.group(1)]:
                        conn.execute('INSERT OR REPLACE INTO web_match_proposals VALUES(?,?,?,?,?)',(item['id'],kind,entity,json.dumps({'matched_label':match.group(1),'source':'page_title_or_alt','not_depiction_proof':True}),'unconfirmed'))

    def probe(self,identity,cancelled,operation='original_download'):
        item=self.repo.image(identity);source=item['source_id'];resource='web:'+identity
        if operation=='thumbnail' and item['role']!='thumbnail':raise RemoteError('명시적인 썸네일 버전만 표시 권한으로 받을 수 있습니다.',blocked=True)
        self.repo.require(source,operation,resource)
        validate_url(item['url'],self.repo.spec(source).domains)
        self.robots(source,item['url'],cancelled)
        from .online_service import download_file
        staging=self.settings.cache/'web'/source/(uuid.uuid4().hex+'.probe')
        result=download_file(self.client(source),item['url'],staging,cancelled,limit=(8 if operation=='thumbnail' else 64)*1024*1024,max_pixels=4_000_000 if operation=='thumbnail' else 40_000_000)
        try:
            # inspect_image enforces format and full decode integrity. Staging has no
            # trusted filename extension: derive it from the actual decoder format.
            with Image.open(staging) as image:fmt=image.format
            suffix={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp','BMP':'.bmp','TIFF':'.tiff'}.get(fmt)
            if suffix is None:raise RemoteError('지원하지 않는 실제 이미지 형식입니다.')
            typed=staging.with_suffix(suffix);staging.rename(typed);staging=typed
            meta=inspect_image(typed)
            meta['mime']=Image.MIME.get(meta['format'],meta['format'])
            self.repo.require(source,operation,resource)
            final=typed.parent/(meta['sha256']+suffix)
            if final.exists():
                if file_hash(final)!=meta['sha256']:raise RemoteError('기존 검사 캐시가 변경되었습니다. 덮어쓰지 않습니다.')
            else:typed.replace(final)
            changed=self.repo.measured(identity,meta,final)
            return {'width':meta['width'],'height':meta['height'],'sha256':meta['sha256'],'rights_review_required':changed}
        finally:staging.unlink(missing_ok=True)

    def download(self,identity,cancelled,confirmed_people=()):
        item=self.repo.image(identity);resource='web:'+identity
        confirmed_people=list(dict.fromkeys(confirmed_people))
        if len(confirmed_people)>100:raise ValueError('한 번에 확인할 인물 수를 초과했습니다.')
        if confirmed_people:
            with self.db.connect() as conn:
                valid={row['id'] for row in conn.execute('SELECT id FROM persons WHERE id IN ('+','.join('?' for _ in confirmed_people)+')',confirmed_people)}
            if valid!=set(confirmed_people):raise ValueError('확인할 인물을 찾을 수 없습니다.')
        self.repo.require(item['source_id'],'original_download',resource)
        if item['role']=='thumbnail':
            from .provided_files import resolve
            version=resolve(self,identity,cancelled)
            item=item|dict(cache_path=version['path'],sha256=version['sha256'],url=version['source_url'])
        else:
            if not item['cache_path'] or not Path(item['cache_path']).is_file():self.probe(identity,cancelled)
            item=self.repo.image(identity)
        self.repo.require(item['source_id'],'original_download',resource)
        cached=Path(item['cache_path'])
        if file_hash(cached)!=item['sha256']:raise RemoteError('검사 캐시가 변경되었습니다. 다시 확인하세요.')
        destination=self.settings.storage/'web'/item['source_id']/(item['sha256']+cached.suffix)
        destination.parent.mkdir(parents=True,exist_ok=True)
        temp=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.part')
        try:
            if not destination.exists():
                shutil.copyfile(cached,temp)
                if cancelled.is_set():raise Cancelled()
                if file_hash(temp)!=item['sha256']:raise RemoteError('보관 복사본 무결성 오류')
                self.repo.require(item['source_id'],'original_download',resource)
                temp.replace(destination)
            elif file_hash(destination)!=item['sha256']:raise RemoteError('기존 보관 파일을 덮어쓰지 않았습니다.')
        finally:temp.unlink(missing_ok=True)
        meta=inspect_image(destination)|dict(managed=1,name=item['title'] or Path(urlsplit(item['url']).path).name or '웹 이미지')
        if cancelled.is_set():raise Cancelled()
        self.repo.require(item['source_id'],'original_download',resource)
        asset,_=self.db.register(meta,origin=item['source_id'])
        self.repo.attach(item,asset)
        published=self.repo.page(item['page_id'])['published']
        if published:
            with self.db.connect() as conn:conn.execute('UPDATE assets SET published=COALESCE(published,?) WHERE id=?',(published,asset))
        if confirmed_people:
            with self.db.connect() as conn:
                for person in set(confirmed_people):
                    if not conn.execute('SELECT 1 FROM persons WHERE id=?',(person,)).fetchone():raise ValueError('확인할 인물을 찾을 수 없습니다.')
                    conn.execute('INSERT OR IGNORE INTO image_person(image_id,person_id) VALUES(?,?)',(asset,person))
                genders=[r[0] for r in conn.execute('SELECT p.gender FROM persons p JOIN image_person ip ON p.id=ip.person_id WHERE ip.image_id=?',(asset,))]
                hidden=bool(genders and all(g=='male' for g in genders))
                conn.execute('INSERT INTO asset_visibility VALUES(?,?,?) ON CONFLICT(asset_id) DO UPDATE SET hidden=excluded.hidden,reason=excluded.reason',(asset,int(hidden),'사용자가 확인한 등장 인물이 모두 남성 · 로컬 라이브러리에서 확인 가능' if hidden else ''))
        result={'asset_id':asset,'sha256':item['sha256']}
        if item['role']=='thumbnail':result['provided_file_only']=True
        return result

    def analyze(self,identity,cancelled,after='',expected_sha=None):
        item=self.repo.image(identity)
        if expected_sha and item['sha256']!=expected_sha:raise RemoteError('분석 중 파일 개정본이 변경됐습니다. 새로 검사를 요청하세요.',blocked=True)
        self.repo.require(item['source_id'],'local_analysis','web:'+identity)
        if after:
            if not expected_sha or not item['dhash']:raise RemoteError('분석 이어가기 근거가 없습니다. 새로 요청하세요.',blocked=True)
            from .perceptual import bands
            bands(item['dhash'])
            value=item['dhash'];bits=int(value,16)
        else:
            if not item['cache_path']:raise RemoteError('검사된 로컬 이미지가 없습니다. 분석 권한이 다운로드 권한을 대신하지 않습니다.')
            path=Path(item['cache_path'])
            if file_hash(path)!=item['sha256']:raise RemoteError('파일이 변경되어 분석하지 않았습니다.')
            with Image.open(path) as image:
                image.draft('L',(18,18))
                image.thumbnail((18,18))
                image=ImageOps.exif_transpose(image).convert('L').resize((9,8))
                pixels=list(image.getdata())
                bits=0
                for y in range(8):
                    for x in range(8):bits=(bits<<1)|int(pixels[y*9+x]>pixels[y*9+x+1])
            value=f'{bits:016x}'
        from .perceptual import store_feature,candidates
        self.repo.require(item['source_id'],'local_analysis','web:'+identity)
        with self.db.connect() as conn:
            current=conn.execute('SELECT sha256 FROM web_images WHERE id=?',(identity,)).fetchone()
            if current['sha256']!=item['sha256']:raise RemoteError('분석 중 파일이 변경됐습니다.',blocked=True)
            conn.execute('UPDATE web_images SET dhash=? WHERE id=?',(value,identity))
            store_feature(conn,identity,item['sha256'],value,self.clock())
            if not after:
                conn.execute("DELETE FROM image_relations WHERE (left_id=? OR right_id=?) AND kind='perceptual_candidate'",(identity,identity))
            rows,more=candidates(conn,identity,item['sha256'],value,cancelled,after)
            count=0;processed=0;limited=False
            previous=conn.execute("SELECT COUNT(*) FROM image_relations WHERE (left_id=? OR right_id=?) AND kind='perceptual_candidate'",(identity,identity)).fetchone()[0]
            for candidate in rows:
                processed+=1
                if cancelled.is_set():raise Cancelled()
                other=conn.execute('SELECT source_id FROM web_images WHERE id=?',(candidate['id'],)).fetchone()
                if not self.repo.allowed_in(conn,other['source_id'],'local_analysis','web:'+candidate['id']):continue
                try:distance=(bits^int(candidate['dhash'],16)).bit_count()
                except (ValueError,TypeError):continue
                if distance<=4:
                    left,right=sorted((identity,candidate['id']))
                    conn.execute('INSERT OR REPLACE INTO image_relations VALUES(?,?,?,?,?)',(left,right,'perceptual_candidate',distance,json.dumps({'algorithm':'dhash64-thumb-v1','not_identity_proof':True,'hashes':{identity:item['sha256'],candidate['id']:candidate['sha256']}})))
                    count+=1
                    if previous+count>=200:
                        limited=True;break
        return {'possible_duplicates':previous+count,'batch_matches':count,'match_limit_reached':limited,'not_identity_proof':True,'compared_candidates':processed,'next_cursor':rows[-1]['id'] if more and rows and not limited else None,'analysis_sha':item['sha256']}

    def best(self,identity):
        item=self.repo.image(identity)
        versions=[x for x in self.repo.images(page_id=item['page_id'],limit=500) if x['logical_key']==item['logical_key']]
        return choose_version(versions,lambda x:self.repo.allowed(x['source_id'],'original_download','web:'+x['id']))

    def preview(self,identity,cancelled):
        item=self.repo.image(identity)
        self.repo.require(item['source_id'],'thumbnail','web:'+identity)
        # Do not turn display permission into permission to fetch an unknown original.
        if not item['cache_path']:
            if item['role']!='thumbnail':raise RemoteError('검사된 이미지가 없습니다. 원본 임시 검사 권한도 별도로 필요합니다.')
            self.probe(identity,cancelled,operation='thumbnail')
            item=self.repo.image(identity)
        self.repo.require(item['source_id'],'thumbnail','web:'+identity)
        if file_hash(Path(item['cache_path']))!=item['sha256']:raise RemoteError('검사 캐시가 변경되었습니다.')
        return decode_preview(item['cache_path'],512)

    def schedule(self,queue):
        for source in self.repo.profiles():
            for page in self.repo.pages(source['id']):
                if json.loads(page['metadata'] or '{}').get('search_managed'):continue
                if page['next_due']<=self.clock() and self.repo.allowed(page['source_id'],'metadata_search'):
                    queue.enqueue('web_scan',{'page_id':page['id']},key='web_scan:'+page['id'],refresh_after=6*3600)

    def execute(self,job,cancelled):
        payload=job['payload'];kind=job['kind']
        if kind=='web_scan':return self.scan(payload['page_id'],cancelled)
        if kind=='web_probe':return self.probe(payload['item_id'],cancelled)
        if kind=='web_download':return self.download(payload['item_id'],cancelled,payload.get('confirmed_people',()))
        if kind=='web_analyze':return self.analyze(payload['item_id'],cancelled,payload.get('after',''),payload.get('analysis_sha'))
        raise ValueError('알 수 없는 웹 출처 작업입니다.')
