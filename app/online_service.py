import hashlib
import json
from pathlib import Path
import time
import uuid
import shutil
from PIL import Image
from .images import inspect_image, file_hash, decode_preview
from .providers.http import RemoteError, Cancelled, validate_url, HOSTS
from .online_repository import OnlineRepository
from .artist_rules import automatic_profile
from .providers.wikimedia import canonical_sha1

MAX_DOWNLOAD=256*1024*1024


source_sha1=canonical_sha1


def download_file(http,url,destination,cancelled,limit=MAX_DOWNLOAD,expected_size=0,expected_sha1=None,max_pixels=100_000_000):
    """Stream to a unique partial file; never publish incomplete/unverified bytes."""
    validate_url(url,getattr(http,'allowed_hosts',HOSTS))
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    temp=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.part')
    sha1=hashlib.sha1();sha256=hashlib.sha256();size=0
    try:
        with http.open(url,cancelled,accept='image/*') as response, temp.open('xb') as output:
            declared=response.headers.get('Content-Length')
            if declared and (int(declared)<0 or int(declared)>limit):raise RemoteError('다운로드 크기 한도를 초과했습니다.')
            while True:
                if cancelled.is_set():raise Cancelled()
                chunk=response.read(256*1024)
                if not chunk:break
                size+=len(chunk)
                if size>limit:raise RemoteError('다운로드 크기 한도를 초과했습니다.')
                output.write(chunk);sha1.update(chunk);sha256.update(chunk)
        if not size or expected_size and size!=expected_size:raise RemoteError('다운로드 파일 크기가 일치하지 않습니다.',retryable=True)
        expected=source_sha1(expected_sha1)
        if expected and sha1.hexdigest()!=expected:raise RemoteError('원본 SHA-1 체크섬이 일치하지 않습니다.',retryable=True)
        # Decode before atomic publication. Small previews are also validated as real images.
        with Image.open(temp) as header:
            if header.format not in ('JPEG','PNG','WEBP','BMP','TIFF') or header.width*header.height>max_pixels:
                raise RemoteError('지원 형식/이미지 픽셀 한도를 확인하세요.')
        decode_preview(temp,1024,pixel_limit=max_pixels)
        if cancelled.is_set():raise Cancelled()
        # Respect pre-existing files: reuse only identical bytes, never overwrite user changes.
        if destination.exists():
            if file_hash(destination)!=sha256.hexdigest():raise RemoteError('기존 보관 파일과 내용이 다릅니다. 덮어쓰지 않았습니다.')
        else:temp.replace(destination)
        return dict(path=str(destination),size=size,sha256=sha256.hexdigest())
    finally:
        temp.unlink(missing_ok=True)


class OnlineService:
    def __init__(self,db,settings,provider,clock=time.time):
        self.db,self.settings,self.provider=db,settings,provider
        self.repo=OnlineRepository(db,clock)
        self.clock=clock
        from .web_service import WebService
        self.web=WebService(db,settings,clock=clock)
        from .image_analysis import ImageAnalysisService
        self.analysis=ImageAnalysisService(self)

    def ready(self,operation='metadata_search'):
        return self.repo.reviewed() and self.web.repo.allowed('wikimedia',operation)

    def require_review(self,operation='metadata_search',resource=None):
        if not self.repo.reviewed():
            raise RemoteError('제공처의 최신 정책/API 확인이 대기 중입니다. 실제 온라인 수집은 아직 활성화하지 않았습니다.',blocked=True)
        self.web.repo.require('wikimedia',operation,resource)

    def run_one(self,cancelled,queue='network',job_id=None):
        job=self.repo.claim(queue,job_id)
        if not job:return None
        activity=getattr(self,'on_job_started',None)
        if activity:activity(queue)
        state='done'
        result=None
        try:
            if cancelled.is_set():raise Cancelled()
            if not job['kind'].startswith('web_') and job['kind']!='image_analyze':self.require_review()
            result,next_payload=self.execute(job,cancelled)
            state='queued' if next_payload is not None else 'done'
            self.repo.finish(job['id'],state,result=result,payload=next_payload,claim_token=job['claim_token'])
        except Cancelled:
            state='queued'
            self.repo.finish(job['id'],'queued','작업을 중단했습니다. 다음 실행에서 이어갑니다.',claim_token=job['claim_token'],refund_attempt=True)
        except RemoteError as error:
            if error.blocked:state,delay='blocked',0
            elif error.retryable and job['attempts']<4:
                state='retry';delay=max(15*2**(job['attempts']-1),error.retry_after or 0)
            else:state,delay='failed',0
            self.repo.finish(job['id'],state,str(error),delay=max(delay,error.retry_after or 0),claim_token=job['claim_token'])
        except Exception as error:
            import logging
            logging.exception('Online job failed')
            state='failed'
            self.repo.finish(job['id'],'failed',str(error),claim_token=job['claim_token'])
        return dict(id=job['id'],kind=job['kind'],state=state,analysis_updated=bool(result and result.get('analysis_updated')),library_changed=state=='done' and (job['kind'] in ('download','web_download') or bool(result and result.get('registered'))))

    def execute(self,job,cancelled):
        kind,payload=job['kind'],job['payload']
        if kind=='image_analyze':return self.analysis.run(payload['entry_id'],cancelled,payload.get('force',False),payload.get('sha256')),None
        if kind.startswith('web_'):
            result=self.web.execute(job,cancelled)
            continuation=(payload|{'after':result['next_cursor'],'analysis_sha':result['analysis_sha']}) if kind=='web_analyze' and result.get('next_cursor') else None
            return result,continuation
        if kind=='discover':
            candidates,cursor=self.provider.discover(payload.get('cursor'),cancelled)
            for candidate in candidates:self.repo.candidate(candidate)
            for start in range(0,len(candidates),20):
                ids=[x['external_id'] for x in candidates[start:start+20]]
                self.repo.enqueue('enrich',{'ids':ids},key='enrich:'+','.join(ids))
            return dict(candidates=len(candidates),complete=cursor is None),({'cursor':cursor} if cursor else None)
        if kind=='enrich':
            items=self.provider.entities(payload['ids'],cancelled)
            registered=0
            for item in items:
                previous=self.repo.candidate_item(item['external_id'])
                candidate=previous|item
                self.repo.candidate(candidate)
                profile=automatic_profile(candidate) if self.settings.values.get('auto_register',True) else None
                if profile and not self.repo.linked(candidate['external_id']):
                    try:
                        self.repo.accept(candidate['external_id'],**profile)
                        registered+=1
                    except ValueError:
                        # Same-name/identity conflicts stay in review and never replace local data.
                        pass
            return dict(updated=len(items),registered=registered),None
        if kind in ('refresh','photos'):
            target=next((x for x in self.repo.targets() if x['id']==payload['target_id']),None)
            if not target:raise ValueError('등록된 갱신 대상이 없습니다.')
            if kind=='refresh':
                items=self.provider.entities([target['external_id']],cancelled)
                if not items:raise RemoteError('외부 식별자가 삭제되었거나 조회되지 않습니다.')
                self.repo.update_target(target['id'],items[0])
                # Complete member IDs are proposals, including men; do not erase local membership.
                members=items[0].get('members',[])
                for identity in members:
                    self.repo.candidate(dict(external_id=identity,kind='person',name=identity,proposed_group=target['external_id']),reason='외부 소속 후보 · 인물/성별/기간 및 실제 소속 확인 필요')
                for start in range(0,len(members),20):
                    ids=members[start:start+20]
                    self.repo.enqueue('enrich',{'ids':ids},key='enrich:'+','.join(ids))
                category=items[0].get('category')
                if category:self.repo.enqueue('photos',{'target_id':target['id'],'cursor':None},refresh_after=6*3600)
                return dict(metadata_updated=True,member_proposals=len(members)),None
            if not target['category']:raise RemoteError('확인된 Commons 카테고리가 없습니다. 이름으로 임의 수집하지 않습니다.')
            images,cursor=self.provider.photos(target['category'],payload.get('cursor'),cancelled)
            self.repo.index_images(target['id'],images,cursor)
            return dict(indexed=len(images),complete=not cursor),({'target_id':target['id'],'cursor':cursor} if cursor else None)
        if kind=='download':
            row=self.repo.image(payload['remote_id'])
            self.require_review('original_download','commons:'+row['id'])
            if row['license_status']!='eligible':raise RemoteError('원본 저장 전 파일별 이용 조건 확인이 필요합니다.',blocked=True)
            local=None
            if row['local_asset_id']:
                with self.db.connect() as conn:local=conn.execute('SELECT * FROM assets WHERE id=?',(row['local_asset_id'],)).fetchone()
            reused=bool(local and Path(local['path']).is_file())
            suffix={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}[row['mime']]
            destination=Path(local['path']) if reused else self.settings.storage/'commons'/('commons-'+row['id']+suffix)
            if not reused:
                with self.db.connect() as conn:cache=conn.execute('SELECT * FROM remote_view_cache WHERE entry_id=?',('commons:'+row['id'],)).fetchone()
                revision=(row['source_sha1'] or '')+'|'+row['original_url']
                if cache and cache['source_revision']==revision and Path(cache['path']).is_file():
                    if file_hash(cache['path'])!=cache['sha256']:raise RemoteError('보기 캐시가 변경됐습니다. 덮어쓰지 않았습니다.')
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    temp=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.part')
                    try:
                        if not destination.exists():
                            with Path(cache['path']).open('rb') as original,temp.open('xb') as output:
                                while chunk:=original.read(1024*1024):
                                    if cancelled.is_set():raise Cancelled()
                                    output.write(chunk)
                            if file_hash(temp)!=cache['sha256']:raise RemoteError('보관 복사본 무결성 오류')
                            self.require_review('original_download','commons:'+row['id'])
                            temp.replace(destination)
                        elif file_hash(destination)!=cache['sha256']:raise RemoteError('기존 보관 파일을 덮어쓰지 않았습니다.')
                    finally:temp.unlink(missing_ok=True)
                else:download_file(self.provider.http,row['original_url'],destination,cancelled,expected_size=row['size'],expected_sha1=row['source_sha1'])
            meta=inspect_image(destination)|dict(name=row['title'].removeprefix('File:'),managed=local['managed'] if reused else 1)
            if reused:
                expected=source_sha1(row['source_sha1'])
                actual=hashlib.sha1()
                with destination.open('rb') as existing:
                    for chunk in iter(lambda:existing.read(1024*1024),b''):
                        if cancelled.is_set():raise Cancelled()
                        actual.update(chunk)
                if meta['sha256']!=local['sha256'] or expected and actual.hexdigest()!=expected:
                    raise RemoteError('이미 보관한 원본이 변경되었거나 다른 개정본입니다. 덮어쓰지 않았습니다.')
            person=group=None
            target=next((x for x in self.repo.targets() if x['id']==payload.get('target_id')),None)
            if target:
                with self.db.connect() as conn:
                    if not conn.execute('SELECT 1 FROM remote_targets WHERE remote_id=? AND target_id=?',(row['id'],target['id'])).fetchone():
                        raise ValueError('이 사진과 등록 대상의 출처 연결이 일치하지 않습니다.')
                # Category membership does not establish who appears in a photograph.
                if target['kind']=='group':group=target['entity_id']
                elif payload.get('person_confirmed'):person=target['entity_id']
            identity,_=self.db.register(meta,person,group,origin='wikimedia')
            with self.db.connect() as conn:
                conn.execute('UPDATE assets SET captured=COALESCE(captured,?),published=COALESCE(published,?) WHERE id=?',(row['captured'],row['published'],identity))
            self.repo.image_path(row['id'],destination,identity)
            return dict(asset_id=identity,sha256=meta['sha256'],reused=reused),None
        raise ValueError('알 수 없는 온라인 작업')

    def thumbnail(self,identity,cancelled):
        row=self.repo.image(identity)
        self.require_review('thumbnail','commons:'+row['id'])
        cached=Path(row['thumbnail_path']) if row['thumbnail_path'] else None
        if cached and cached.is_file():return decode_preview(cached,512,pixel_limit=4_000_000)
        self.require_review()
        if row['license_status']!='eligible':raise RemoteError('파일별 이용 조건을 확인하기 전에는 썸네일도 받지 않습니다.',blocked=True)
        if not row['thumbnail_url']:raise RemoteError('제공처 썸네일이 없습니다. 원본을 대신 자동 다운로드하지 않습니다.')
        path=self.settings.cache/'online'/('commons-'+row['id']+'.preview')
        download_file(self.provider.http,row['thumbnail_url'],path,cancelled,limit=8*1024*1024,max_pixels=4_000_000)
        self.repo.image_path(identity,path)
        return decode_preview(path,512,pixel_limit=4_000_000)
