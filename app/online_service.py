import hashlib
import json
from pathlib import Path
import time
import uuid
from .images import inspect_image, file_hash, decode_preview
from .providers.http import RemoteError, Cancelled, validate_url
from .online_repository import OnlineRepository
from .artist_rules import automatic_profile
from .providers.wikimedia import canonical_sha1

MAX_DOWNLOAD=256*1024*1024


source_sha1=canonical_sha1


def download_file(http,url,destination,cancelled,limit=MAX_DOWNLOAD,expected_size=0,expected_sha1=None):
    """Stream to a unique partial file; never publish incomplete/unverified bytes."""
    validate_url(url)
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
        decode_preview(temp,1024)
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

    def require_review(self):
        if not self.repo.reviewed():
            raise RemoteError('제공처의 최신 정책/API 확인이 대기 중입니다. 실제 온라인 수집은 아직 활성화하지 않았습니다.',blocked=True)

    def run_one(self,cancelled):
        job=self.repo.claim()
        if not job:return None
        state='done'
        result=None
        try:
            if cancelled.is_set():raise Cancelled()
            self.require_review()
            result,next_payload=self.execute(job,cancelled)
            state='queued' if next_payload is not None else 'done'
            self.repo.finish(job['id'],state,result=result,payload=next_payload)
        except Cancelled:
            state='queued'
            self.repo.finish(job['id'],'queued','작업을 중단했습니다. 다음 실행에서 이어갑니다.')
        except RemoteError as error:
            if error.blocked:state,delay='blocked',0
            elif error.retryable and job['attempts']<4:
                state='retry';delay=max(15*2**(job['attempts']-1),error.retry_after or 0)
            else:state,delay='failed',0
            self.repo.finish(job['id'],state,str(error),delay=delay)
        except Exception as error:
            import logging
            logging.exception('Online job failed')
            state='failed'
            self.repo.finish(job['id'],'failed',str(error))
        return dict(id=job['id'],kind=job['kind'],state=state,library_changed=state=='done' and (job['kind']=='download' or bool(result and result.get('registered'))))

    def execute(self,job,cancelled):
        kind,payload=job['kind'],job['payload']
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
            if row['license_status']!='eligible':raise RemoteError('원본 저장 전 파일별 이용 조건 확인이 필요합니다.',blocked=True)
            local=None
            if row['local_asset_id']:
                with self.db.connect() as conn:local=conn.execute('SELECT * FROM assets WHERE id=?',(row['local_asset_id'],)).fetchone()
            reused=bool(local and Path(local['path']).is_file())
            suffix={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}[row['mime']]
            destination=Path(local['path']) if reused else self.settings.storage/'commons'/('commons-'+row['id']+suffix)
            if not reused:
                download_file(self.provider.http,row['original_url'],destination,cancelled,expected_size=row['size'],expected_sha1=row['source_sha1'])
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
            identity,_=self.db.register(meta,person,group)
            with self.db.connect() as conn:
                conn.execute('UPDATE assets SET captured=COALESCE(captured,?),published=COALESCE(published,?) WHERE id=?',(row['captured'],row['published'],identity))
            self.repo.image_path(row['id'],destination,identity)
            return dict(asset_id=identity,sha256=meta['sha256'],reused=reused),None
        raise ValueError('알 수 없는 온라인 작업')

    def thumbnail(self,identity,cancelled):
        row=self.repo.image(identity)
        cached=Path(row['thumbnail_path']) if row['thumbnail_path'] else None
        if cached and cached.is_file():return decode_preview(cached,512)
        self.require_review()
        if row['license_status']!='eligible':raise RemoteError('파일별 이용 조건을 확인하기 전에는 썸네일도 받지 않습니다.',blocked=True)
        if not row['thumbnail_url']:raise RemoteError('제공처 썸네일이 없습니다. 원본을 대신 자동 다운로드하지 않습니다.')
        path=self.settings.cache/'online'/('commons-'+row['id']+'.preview')
        download_file(self.provider.http,row['thumbnail_url'],path,cancelled,limit=8*1024*1024)
        self.repo.image_path(identity,path)
        return decode_preview(path,512)
