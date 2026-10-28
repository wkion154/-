"""Permission-gated, single-process-at-a-time restoration with separate outputs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from .related import RelatedRepository
from .source_repository import SourceRepository
from .providers.http import Cancelled
from .super_resolution import VERSION,MODEL_HASHES,available,model_path,diagnostics
from .face_reference import VERSION as FACE_VERSION,available as face_available

OPS=('local_analysis','ai_restore_input','result_storage')


class UpscaleService:
    def __init__(self,db,settings):
        self.db,self.settings=db,settings;self.related=RelatedRepository(db);self.sources=SourceRepository(db)
        with db.connect() as c:c.execute('CREATE INDEX IF NOT EXISTS extension_image_kind_created ON extension_records(image_id,kind,created DESC)')
        self.lock=threading.Lock();self.cache=Path(settings.cache)/'upscale-results';self.cache.mkdir(parents=True,exist_ok=True)
    def require(self,identity,conn=None):
        def check(c):
            origins=self.related._origins(identity)
            for source,resource in origins:
                if resource and all(self.sources.allowed_in(c,source,op,resource) for op in OPS):return
            raise ValueError('사진의 분석·AI 입력·결과 저장 권한을 각각 확인하세요.')
        if conn is not None:check(conn)
        else:
            with self.db.connect() as c:check(c)
    def people(self,identity,conn=None):
        sql="SELECT p.id,p.name FROM image_person ip JOIN persons p ON p.id=ip.person_id WHERE ip.image_id=? AND ip.status IN ('user_confirmed','confirmed') ORDER BY p.name LIMIT 33"
        if conn is not None:return dict(conn.execute(sql,(identity,)).fetchall())
        with self.db.connect() as c:return dict(c.execute(sql,(identity,)).fetchall())
    def audit(self,identity,conn):
        result=[]
        for source,resource in self.related._origins(identity):
            if resource and all(self.sources.allowed_in(conn,source,op,resource) for op in OPS):
                result.append(dict(source=source,resource=resource,source_permissions=self.sources.permissions(source),resource_permissions=self.sources.permissions(source,resource)))
                break
        return result
    def recover(self):
        # Called once at app startup, after its single-instance guard.
        with self.db.connect() as c:
            for row in c.execute("SELECT id,payload FROM extension_records WHERE kind='upscale_job' AND json_extract(payload,'$.state')='running'"):
                data=json.loads(row['payload'])
                if data.get('state')=='running':
                    data.update(state='interrupted',error='앱이 종료되어 중단된 작업입니다. 다시 실행할 수 있습니다.')
                    c.execute('UPDATE extension_records SET payload=? WHERE id=?',(json.dumps(data,ensure_ascii=False),row['id']))
    def _record(self,record_id,identity,kind,data):
        with self.db.connect() as c:c.execute('INSERT INTO extension_records VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(record_id,identity,kind,'FSRCNN',VERSION,json.dumps(data,ensure_ascii=False),time.time()))
    def recent(self,identity):
        with self.db.connect() as c:
            return [dict(id=r['id'],**json.loads(r['payload'])) for r in c.execute("SELECT id,payload FROM extension_records WHERE image_id=? AND kind='upscale_result' ORDER BY created DESC LIMIT 24",(identity,))]
    def _snapshot(self,row,directory,cancelled):
        self.require(row['id'])
        path=Path(row['path']);stat=path.stat()
        if stat.st_size>128*1024*1024:raise ValueError('입력 파일 한도 128MiB를 초과합니다.')
        target=Path(directory)/(row['id']+'.input');digest=hashlib.sha256()
        with path.open('rb') as source,target.open('xb') as dest:
            for block in iter(lambda:source.read(1024*1024),b''):
                if cancelled.is_set():raise Cancelled()
                digest.update(block);dest.write(block)
        current=path.stat()
        if digest.hexdigest()!=row['sha256'] or (stat.st_size,stat.st_mtime_ns)!=(current.st_size,current.st_mtime_ns):raise ValueError('입력이 변경됐습니다. 파일을 다시 등록하세요.')
        return dict(id=row['id'],sha256=row['sha256'],path=str(target),source_signature=[str(path.resolve()),stat.st_size,stat.st_mtime_ns])
    def _fresh(self,inputs,conn=None):
        for item in inputs:
            self.require(item['id'],conn)
            if conn is None:row=self.related.asset(item['id'])
            else:
                row=conn.execute('SELECT * FROM assets WHERE id=?',(item['id'],)).fetchone()
            if not row or row['sha256']!=item['sha256']:raise ValueError('입력 개정본이 바뀌었습니다.')
            stat=Path(row['path']).stat()
            if [str(Path(row['path']).resolve()),stat.st_size,stat.st_mtime_ns]!=item['source_signature']:raise ValueError('작업 중 입력 파일이 변경됐습니다.')
    def _person(self,identity,reference,person,conn=None):
        if reference and (not person or person not in self.people(identity,conn) or person not in self.people(reference,conn)):
            raise ValueError('두 사진에 같은 인물이 등장한다고 확인한 연결이 필요합니다.')
        # Multi-person photos require a face-region model that this engine does not have.
        if reference and (len(self.people(identity,conn))!=1 or len(self.people(reference,conn))!=1):raise ValueError('여러 인물의 참조 복원은 지원하지 않습니다.')
    def _candidate(self,identity,person,cancelled):
        found=self.related.search(identity,cancelled,reference=True,person=person)
        rows=[]
        for row in found['rows']:
            from .related import calendar_day
            from datetime import date
            seed_day=calendar_day(found['seed']['captured']);reference_day=calendar_day(row['captured'])
            if seed_day and reference_day and abs((date.fromisoformat(seed_day)-date.fromisoformat(reference_day)).days)>180:continue
            try:self.require(row['id']);self._person(identity,row['id'],person)
            except ValueError:continue
            if row['width']*row['height']>found['seed']['width']*found['seed']['height']:rows.append(row)
        return rows[:8]
    def run(self,identity,cancelled,scale=2,mode='general',person=None,force=False,progress=lambda *_:None,backend='cpu'):
        if mode not in ('general','reference'):raise ValueError('유효한 모드를 선택하세요.')
        model_path(scale)
        if backend not in ('cpu','cuda'):raise ValueError('지원하지 않는 실행 장치입니다.')
        if backend=='cuda' and not diagnostics()['cuda_ready']:raise ValueError('CUDA 실행 환경 진단을 통과하지 못했습니다.')
        if not available():raise ValueError('선택 업스케일 의존성을 설치하세요: requirements-upscale.lock')
        if not self.lock.acquire(blocking=False):raise ValueError('이미 복원 작업을 실행 중입니다. 완료하거나 취소한 뒤 다시 실행하세요.')
        deadline=time.monotonic()+300
        job_id=uuid.uuid4().hex;job=dict(state='running',mode=mode,scale=scale,started=time.time())
        try:
            if cancelled.is_set():raise Cancelled()
            self.require(identity);seed=self.related.asset(identity)
            if mode=='reference' and not face_available():raise ValueError('참조 얼굴 모델을 설치하세요: requirements-reference.lock')
            self._record(job_id,identity,'upscale_job',job)
            if mode=='reference':
                people=self.people(identity)
                if person is None and len(people)==1:person=next(iter(people))
                candidates=self._candidate(identity,person,cancelled)
                if not candidates:raise ValueError('권한과 인물 연결이 확인된 고해상도 참조 후보가 없습니다.')
            else:candidates=[None]
            rejected=[]
            for reference in candidates:
                if cancelled.is_set():raise Cancelled()
                try:
                    self._person(identity,reference['id'] if reference else None,person)
                    with tempfile.TemporaryDirectory(prefix='work-',dir=self.cache) as directory:
                        inputs=[self._snapshot(seed,directory,cancelled)]
                        if reference:inputs.append(self._snapshot(reference,directory,cancelled))
                        config=dict(backend=backend,reference_engine=FACE_VERSION if reference else None,mode=mode,scale=scale,strength=.15,engine=VERSION,model_sha256=MODEL_HASHES[scale],input_sha256=seed['sha256'],reference_id=reference['id'] if reference else None,reference_sha256=reference['sha256'] if reference else None,person=person if reference else None)
                        key=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
                        cached=next((r for r in self.recent(identity) if r['key']==key and Path(r['path']).is_file()),None) if not force else None
                        if cached and self._verify_output(cached,cancelled):
                            with self.db.connect() as c:
                                c.execute('BEGIN IMMEDIATE');self._fresh(inputs,c);self._person(identity,reference['id'] if reference else None,person,c)
                            job.update(state='done',reused=True,result_id=cached['id']);self._record(job_id,identity,'upscale_job',job)
                            return dict(cached,reused=True)
                        output=Path(directory)/'result.png'
                        request=dict(config,timeout_seconds=max(0,deadline-time.monotonic()),input=inputs[0],reference=inputs[1] if reference else None,output=str(output))
                        metrics=self._worker(request,directory,cancelled,progress)
                        self._fresh(inputs)
                        if cancelled.is_set():raise Cancelled()
                        result_id=uuid.uuid4().hex;destination=self.cache/(result_id+'.png')
                        result=dict(key=key,path=str(destination),config=config,metrics=metrics,inputs=[{k:v for k,v in item.items() if k!='path'} for item in inputs],created=time.time(),output_sha256=self._hash(output,cancelled),rejected=rejected,reused=False)
                        try:
                            with self.db.connect() as c:
                                c.execute('BEGIN IMMEDIATE');self._fresh(inputs,c);self._person(identity,reference['id'] if reference else None,person,c)
                                result['rights']={item['id']:self.audit(item['id'],c) for item in inputs}
                                if cancelled.is_set():raise Cancelled()
                                output.replace(destination)
                                c.execute('INSERT INTO extension_records VALUES(?,?,?,?,?,?,?)',(result_id,identity,'upscale_result','FSRCNN',VERSION,json.dumps(result,ensure_ascii=False),time.time()))
                        except BaseException:destination.unlink(missing_ok=True);raise
                        job.update(state='done',result_id=result_id);self._record(job_id,identity,'upscale_job',job)
                        return dict(id=result_id,**result)
                except (ValueError,OSError) as error:
                    if not reference:raise
                    rejected.append(dict(id=reference['id'],reason=str(error)))
            job['rejected']=rejected
            reason=rejected[0]['reason'] if rejected else '유효한 후보 없음'
            raise ValueError(f'참조 후보 {len(rejected)}개의 정렬/품질 검증 실패로 채택을 보류했습니다. {reason} 일반 모드를 사용할 수 있습니다.')
        except BaseException as error:
            job.update(state='cancelled' if isinstance(error,Cancelled) or cancelled.is_set() else 'failed',error=str(error)[:6000])
            with self.db.connect() as c:exists=c.execute('SELECT 1 FROM assets WHERE id=?',(identity,)).fetchone()
            if exists:self._record(job_id,identity,'upscale_job',job)
            raise
        finally:self.lock.release()
    @staticmethod
    def _hash(path,cancelled):
        digest=hashlib.sha256()
        with open(path,'rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):
                if cancelled.is_set():raise Cancelled()
                digest.update(block)
        return digest.hexdigest()
    def _verify_output(self,result,cancelled):
        return self._hash(result['path'],cancelled)==result['output_sha256']
    def _worker(self,request,directory,cancelled,progress):
        config=Path(directory)/'request.json';config.write_text(json.dumps(request),'utf-8')
        log=Path(directory)/'worker.log';process=None;start=time.monotonic()
        environment=os.environ.copy();environment.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',MPLBACKEND='Agg',MPLCONFIGDIR=str(Path(directory)/'matplotlib'))
        try:
            with log.open('wb') as stream:
                process=subprocess.Popen([sys.executable,'-m','app.super_resolution',str(config)],cwd=Path(__file__).resolve().parents[1],stdout=stream,stderr=subprocess.STDOUT,env=environment,creationflags=getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0))
                while process.poll() is None:
                    if cancelled.wait(.08):raise Cancelled()
                    if time.monotonic()-start>request.get('timeout_seconds',300):raise ValueError('복원 작업의 5분 제한을 초과했습니다. 더 작은 사진으로 시도하세요.')
                    if log.stat().st_size>2*1024*1024:raise ValueError('복원 로그 한도를 초과했습니다.')
                    lines=log.read_text(errors='replace').splitlines()
                    for line in reversed(lines):
                        try:value=json.loads(line)
                        except ValueError:continue
                        if 'progress' in value:
                            progress(value['progress'],f"CPU 타일 처리 · {value['progress']*100:.0f}%");break
            entries=[]
            for line in log.read_text(errors='replace').splitlines():
                try:entries.append(json.loads(line))
                except ValueError:pass
            result=next((e['result'] for e in reversed(entries) if 'result' in e),None)
            if process.returncode or result is None:raise ValueError(next((e['error'] for e in reversed(entries) if 'error' in e),'복원 실행에 실패했습니다. 선택 의존성을 확인하세요.'))
            return result
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait()
    def authorized_result(self,identity,result_id,cancelled):
        result=next((r for r in self.recent(identity) if r['id']==result_id),None)
        if not result:raise ValueError('결과를 찾을 수 없습니다.')
        self._fresh(result['inputs']);config=result['config'];self._person(identity,config['reference_id'],config['person'])
        if not self._verify_output(result,cancelled):raise ValueError('결과 파일이 변경됐습니다. 다시 실행하세요.')
        return result
    def export(self,identity,result_id,cancelled):
        result=self.authorized_result(identity,result_id,cancelled)
        folder=Path(self.settings.storage)/'upscale';folder.mkdir(parents=True,exist_ok=True)
        target=folder/(result_id+'-'+uuid.uuid4().hex[:8]+'.png');metadata=target.with_suffix('.json')
        try:
            with open(result['path'],'rb') as source,target.open('xb') as dest:
                for block in iter(lambda:source.read(1024*1024),b''):
                    if cancelled.is_set():raise Cancelled()
                    dest.write(block)
            self.authorized_result(identity,result_id,cancelled)
            with metadata.open('x',encoding='utf-8') as f:json.dump(dict(result,exported_path=str(target)),f,ensure_ascii=False,indent=2)
        except BaseException:target.unlink(missing_ok=True);metadata.unlink(missing_ok=True);raise
        return str(target)
    def delete_cache(self,identity,result_id):
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute("SELECT payload FROM extension_records WHERE id=? AND image_id=? AND kind='upscale_result'",(result_id,identity)).fetchone()
            if not row:return
            path=Path(json.loads(row[0])['path'])
            if path.parent.resolve()!=self.cache.resolve() or path.name!=result_id+'.png':raise ValueError('결과 경로를 확인하세요.')
            path.unlink(missing_ok=True);c.execute('DELETE FROM extension_records WHERE id=?',(result_id,))

    def export_original(self,identity,cancelled):
        self.require(identity);row=self.related.asset(identity)
        folder=Path(self.settings.storage)/'upscale';folder.mkdir(parents=True,exist_ok=True)
        target=folder/('original-'+identity+'-'+uuid.uuid4().hex[:8]+Path(row['path']).suffix.lower())
        try:
            with open(row['path'],'rb') as source,target.open('xb') as dest:
                for block in iter(lambda:source.read(1024*1024),b''):
                    if cancelled.is_set():raise Cancelled()
                    dest.write(block)
            self.require(identity)
            if self._hash(target,cancelled)!=row['sha256']:raise ValueError('원본 내용이 등록 해시와 다릅니다.')
        except BaseException:target.unlink(missing_ok=True);raise
        return str(target)
