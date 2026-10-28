"""Permission-gated, incremental offline analysis; never downloads its input."""
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from PIL import Image,ImageOps
from .library import LibraryRepository
from .analysis_filters import analysis_permission_sql
from .color_analysis import COLOR_VERSION,palette
from .vision_analysis import VISION_VERSION,available,COMPOSITIONS,COUNTS
from .providers.http import RemoteError,Cancelled


class ImageAnalysisService:
    def __init__(self,online):
        self.online=online;self.db=online.db;self.settings=online.settings;self.repo=LibraryRepository(self.db)
        self.vision=None;self.vision_lock=threading.Lock()
    @property
    def engine(self):return COLOR_VERSION+'+'+(VISION_VERSION if self.settings.values.get('analysis_vision',True) and available() else 'color-only')
    def allowed(self,identity,conn=None):
        sql='SELECT 1 FROM gallery_items e WHERE e.id=? AND '+analysis_permission_sql()
        if conn is not None:return conn.execute(sql,(identity,)).fetchone() is not None
        with self.db.connect() as c:return c.execute(sql,(identity,)).fetchone() is not None
    def require(self,identity,conn=None):
        if not self.allowed(identity,conn):raise RemoteError('이 사진의 로컬 이미지 분석 권한을 먼저 확인하세요.',blocked=True)
    def path(self,row):
        if row['kind']=='asset':return Path(row['path'])
        if row['kind']=='web':
            item=self.online.web.repo.image(row['ref_id'])
            if item['cache_path'] and item['sha256']==row['sha256']:return Path(item['cache_path'])
        else:
            with self.db.connect() as c:cached=c.execute('SELECT * FROM remote_view_cache WHERE entry_id=?',(row['id'],)).fetchone()
            item=self.online.repo.image(row['ref_id'])
            revision=(item['source_sha1'] or '')+'|'+item['original_url']
            if cached and cached['sha256']==row['sha256'] and cached['source_revision']==revision:return Path(cached['path'])
        raise RemoteError('분석할 검증 파일이 없습니다. 분석 작업은 원본을 대신 다운로드하지 않습니다.',blocked=True)
    def mark_stale(self,identity):
        with self.db.connect() as c:c.execute('UPDATE image_analysis SET stale=1 WHERE entry_id=? AND stale=0',(identity,))
    def run(self,identity,cancelled,force=False,expected_sha=None):
        self.require(identity)
        if cancelled.is_set():raise Cancelled()
        row=self.repo.entry(identity)
        if expected_sha and row['sha256']!=expected_sha:raise RemoteError('이전 개정본의 분석 요청입니다. 현재 사진을 새로 요청하세요.',blocked=True)
        if not row['sha256']:raise RemoteError('검증된 파일 해시가 없습니다. 원본 확인 후 분석하세요.',blocked=True)
        path=self.path(row)
        try:stat=path.stat()
        except OSError:self.mark_stale(identity);raise RemoteError('분석 파일 경로를 확인하세요.') from None
        signature=json.dumps([str(path.resolve()),stat.st_size,stat.st_mtime_ns])
        with self.db.connect() as c:previous=c.execute('SELECT * FROM image_analysis WHERE entry_id=?',(identity,)).fetchone()
        engine=self.engine
        if not force and previous and previous['sha256']==row['sha256'] and previous['engine']==engine and previous['signature']==signature and not previous['stale']:
            self.require(identity);return dict(analysis_updated=False,reused=True,entry_id=identity)
        digest=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):
                if cancelled.is_set():raise Cancelled()
                digest.update(chunk)
        if digest.hexdigest()!=row['sha256']:
            self.mark_stale(identity);raise RemoteError('분석 원본이 등록한 내용과 다릅니다. 파일을 다시 등록/확인하세요.')
        if cancelled.is_set():raise Cancelled()
        if not force:
            with self.db.connect() as c:
                c.execute('BEGIN IMMEDIATE');self.require(identity,c)
                prior=c.execute('SELECT x.* FROM image_analysis x JOIN gallery_items g ON g.id=x.entry_id AND g.sha256=x.sha256 WHERE x.sha256=? AND x.engine=? AND x.stale=0 AND '+analysis_permission_sql('g')+" AND json_extract(x.payload,'$.vision_status')<>'error' LIMIT 1",(row['sha256'],engine)).fetchone()
                if prior:
                    stat_now=path.stat()
                    fresh=c.execute('SELECT sha256 FROM gallery_items WHERE id=?',(identity,)).fetchone()
                    if (stat_now.st_size,stat_now.st_mtime_ns)!=(stat.st_size,stat.st_mtime_ns) or fresh[0]!=row['sha256']:raise RemoteError('분석 재사용 중 입력이 변경됐습니다.')
                    bins=[dict(p) for p in c.execute('SELECT * FROM analysis_colors WHERE entry_id=?',(prior['entry_id'],))]
                    c.execute('DELETE FROM image_analysis WHERE entry_id=?',(identity,))
                    c.execute('INSERT INTO image_analysis VALUES(?,?,?,?,?,?,?,?,?,?,0,?)',(identity,row['sha256'],engine,signature,prior['composition'],prior['confidence'],prior['count_class'],prior['face_count'],prior['region_status'],prior['payload'],time.time()))
                    for p in bins:c.execute('INSERT INTO analysis_colors(entry_id,region,hex,l,a,b,weight,total) VALUES(?,?,?,?,?,?,?,?)',(identity,p['region'],p['hex'],p['l'],p['a'],p['b'],p['weight'],p['total']))
                    return dict(analysis_updated=True,reused=True,entry_id=identity,vision_status=json.loads(prior['payload']).get('vision_status'))
        with Image.open(path) as image:
            if image.width*image.height>40_000_000:raise RemoteError('분석 한도 4천만 픽셀을 초과합니다. 뷰어와 분석 한도는 별도입니다.')
            image.draft('RGB',(768,768));image=ImageOps.exif_transpose(image)
            image.thumbnail((768,768),Image.Resampling.LANCZOS);image=image.convert('RGBA')
            result=dict(composition='unknown',confidence=0.,count_class='unknown',faces=[],landmarks=[],region_status='unavailable',reasons=['구도 분석을 사용하지 않았습니다.'],vision_status='not_installed' if not available() else 'disabled')
            masks={}
            if self.settings.values.get('analysis_vision',True) and available():
                try:
                    with self.vision_lock:
                        if cancelled.is_set():raise Cancelled()
                        if self.vision is None:
                            os.environ.setdefault('MPLBACKEND','Agg')
                            os.environ.setdefault('MPLCONFIGDIR',str(self.settings.root/'analysis-runtime'/'matplotlib'))
                            from .vision_analysis import VisionEngine
                            self.vision=VisionEngine()
                        # Transparent pixels are composited only for detection;
                        # occupancy uses the original alpha-weighted colors.
                        background=Image.new('RGB',image.size,'white');background.paste(image,mask=image.getchannel('A'))
                        result,masks=self.vision.analyze(background,cancelled);result['vision_status']='ready'
                except Cancelled:raise
                except Exception as error:result.update(vision_status='error',vision_error=str(error),reasons=['구도 분석에 실패해 미확정입니다. 색상 결과는 별도로 사용합니다.'])
            colors={'whole':palette(image)}
            for region,mask in masks.items():
                if cancelled.is_set():raise Cancelled()
                bins=palette(image,mask)
                if bins:colors[region]=bins
        current=path.stat()
        if (current.st_size,current.st_mtime_ns)!=(stat.st_size,stat.st_mtime_ns):
            self.mark_stale(identity);raise RemoteError('분석 중 파일이 바뀌어 결과를 저장하지 않았습니다.')
        if cancelled.is_set():raise Cancelled()
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE');self.require(identity,c)
            fresh=c.execute('SELECT sha256 FROM gallery_items WHERE id=?',(identity,)).fetchone()
            if fresh is None or fresh[0]!=row['sha256']:raise RemoteError('분석 중 입력 개정본이 변경됐습니다.',blocked=True)
            c.execute('DELETE FROM image_analysis WHERE entry_id=?',(identity,))
            c.execute('INSERT INTO image_analysis VALUES(?,?,?,?,?,?,?,?,?,?,0,?)',(identity,row['sha256'],engine,signature,result['composition'],result['confidence'],result['count_class'],len(result['faces']) if result['vision_status']=='ready' else None,result['region_status'],json.dumps(result,ensure_ascii=False),time.time()))
            for region,bins in colors.items():
                for p in bins:c.execute('INSERT INTO analysis_colors(entry_id,region,hex,l,a,b,weight,total) VALUES(?,?,?,?,?,?,?,?)',(identity,region,p['hex'],p['l'],p['a'],p['b'],p['weight'],p['total']))
        return dict(analysis_updated=True,entry_id=identity,composition=result['composition'],vision_status=result['vision_status'],regions=list(colors))
    def details(self,identity):
        row=self.repo.entry(identity)
        with self.db.connect() as c:
            analysis=c.execute('SELECT * FROM image_analysis WHERE entry_id=? AND sha256=? AND stale=0',(identity,row['sha256'])).fetchone()
            override=c.execute('SELECT * FROM analysis_overrides WHERE entry_id=? AND sha256=?',(identity,row['sha256'])).fetchone()
            allowed=self.allowed(identity,c)
            colors=[dict(r) for r in c.execute('SELECT region,hex,weight,total FROM analysis_colors WHERE entry_id=? ORDER BY region,weight DESC',(identity,))] if analysis and allowed else []
        return dict(entry=row,allowed=allowed,analysis=dict(analysis) if analysis and allowed else None,override=dict(override) if override else None,colors=colors)
    def override(self,identity,composition,count_class,note):
        if composition not in COMPOSITIONS or count_class not in COUNTS or not note.strip() or len(note)>2000:raise ValueError('분류와 확인 근거를 입력하세요.')
        row=self.repo.entry(identity)
        if not row['sha256']:raise ValueError('검증된 파일을 확인한 후 수동 구도를 기록하세요.')
        with self.db.connect() as c:c.execute('INSERT INTO analysis_overrides VALUES(?,?,?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET sha256=excluded.sha256,composition=excluded.composition,count_class=excluded.count_class,note=excluded.note,updated=excluded.updated',(identity,row['sha256'],composition,count_class,note.strip(),time.time()))
    def enqueue(self,identity,force=False):
        row=self.repo.entry(identity)
        return self.online.repo.enqueue('image_analyze',{'entry_id':identity,'sha256':row['sha256'],'force':force},key='image_analyze:'+identity+':'+str(row['sha256'])+':'+self.engine,force=force)

    def schedule(self,queue):
        if not self.settings.values.get('analysis_auto',False):return
        with self.db.connect() as c:
            rows=c.execute("SELECT e.id FROM gallery_items e WHERE e.sha256 IS NOT NULL AND (e.kind='asset' OR e.has_preview=1 OR EXISTS(SELECT 1 FROM remote_view_cache v WHERE v.entry_id=e.id)) AND "+analysis_permission_sql()+" AND (e.kind='asset' OR e.linked_asset_id IS NULL) AND NOT EXISTS(SELECT 1 FROM image_analysis x WHERE x.entry_id=e.id AND x.sha256=e.sha256 AND x.engine=? AND x.stale=0) AND NOT EXISTS(SELECT 1 FROM sync_jobs j WHERE j.job_key='image_analyze:'||e.id||':'||e.sha256||':'||? AND j.state IN ('failed','blocked')) ORDER BY e.registered DESC LIMIT 16",(self.engine,self.engine)).fetchall()
        for row in rows:self.enqueue(row[0])
    def close(self):
        with self.vision_lock:
            if self.vision:self.vision.close();self.vision=None
