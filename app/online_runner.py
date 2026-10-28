"""Two source-isolated network slots, one local-analysis slot, foreground yielding."""
from collections import deque
from contextlib import contextmanager
import threading
import time
import uuid
from PySide6.QtCore import QObject,QTimer,Signal,QThread
from .workers import Tasks

class OnlineRunner(QObject):
    changed=Signal()
    library_changed=Signal()
    error=Signal(str)
    state_changed=Signal(str)
    started=Signal(str,str)
    NETWORK_SLOTS=2

    def __init__(self,service,settings,parent=None):
        super().__init__(parent)
        self.service,self.settings=service,settings
        self.tasks=Tasks(self,self.NETWORK_SLOTS)
        self.analysis_tasks=Tasks(self,1)
        self.analysis_tasks.pool.setThreadPriority(QThread.Priority.LowPriority)
        self.closed=False;self.paused=False;self.last_schedule=0;self.failed_jobs=set();self.last_state='준비됨'
        self.flights={};self.active_tokens=set();self.holds=set();self.quiet_until=0
        self.context=threading.local();self.schedule_lock=threading.Lock()
        self.started.connect(self.mark_active)
        self.service.on_job_started=lambda queue:self.started.emit(getattr(self.context,'token',''),queue)
        self.requests={'network':deque(),'analysis':deque()}
        service.repo.recover()
        self.timer=QTimer(self);self.timer.setInterval(1500);self.timer.timeout.connect(self.tick);self.timer.start()

    @property
    def running(self):return any(item['queue']=='network' for item in self.flights.values())
    @property
    def analysis_running(self):return any(item['queue']=='analysis' for item in self.flights.values())
    @property
    def foreground_busy(self):return bool(self.holds) or time.monotonic()<self.quiet_until

    @contextmanager
    def foreground(self):
        token=uuid.uuid4().hex;self.holds.add(token);self.publish_state()
        try:yield
        finally:self.holds.discard(token);self.publish_state()

    def defer(self,seconds=.4):self.quiet_until=max(self.quiet_until,time.monotonic()+seconds)

    def request(self,identity,queue='network'):
        if self.closed:return
        if queue not in self.requests:raise ValueError('알 수 없는 작업 큐입니다.')
        if identity not in self.requests[queue] and not any(f['identity']==identity for f in self.flights.values()):self.requests[queue].append(identity)
        self.tick()

    def tick(self,manual=False):
        if self.closed:return
        automatic=((self.settings.values.get('online_enabled',True) and not self.paused) or manual) and not self.foreground_busy
        configured=self.settings.values.get('network_workers',self.NETWORK_SLOTS)
        network_slots=configured if type(configured) is int and configured in (1,2) else self.NETWORK_SLOTS
        for queue,capacity in (('network',network_slots),('analysis',1)):
            used=sum(f['queue']==queue for f in self.flights.values())
            for _ in range(capacity-used):
                identity=self.requests[queue].popleft() if self.requests[queue] else None
                if not automatic and not identity:break
                self.launch(queue,identity,automatic)

    def mark_active(self,token,queue):
        if self.closed or token not in self.flights:return
        self.active_tokens.add(token);self.publish_state()

    def publish_state(self):
        if self.closed:return
        queues={self.flights[token]['queue'] for token in self.active_tokens if token in self.flights}
        if {'network','analysis'}.issubset(queues):message='동기화·검사 중'
        elif 'network' in queues:message='동기화 중'
        elif 'analysis' in queues:message='이미지 검사 중'
        elif self.paused:message='갱신 일시 중지'
        elif self.foreground_busy:message='감상 우선 · 갱신 대기'
        elif self.failed_jobs:message='일부 출처 확인 필요'
        else:message=self.last_state
        self.state_changed.emit(message)

    def launch(self,queue,identity,automatic):
        token=uuid.uuid4().hex;cancel=threading.Event()
        self.flights[token]=dict(queue=queue,identity=identity,cancel=cancel)
        tasks=self.tasks if queue=='network' else self.analysis_tasks
        def work(_):
            self.context.token=token
            if queue=='network' and automatic:
                with self.schedule_lock:
                    if time.monotonic()-self.last_schedule>=60:
                        ready=self.service.ready() if hasattr(self.service,'ready') else self.service.repo.reviewed()
                        if ready:self.service.repo.schedule()
                        if hasattr(self.service,'web'):self.service.web.schedule(self.service.repo)
                        self.last_schedule=time.monotonic()
            if identity:
                result=self.service.run_one(cancel,queue=queue,job_id=identity)
                if result is None:
                    return dict(id=identity,state=self.service.repo.job_state(identity),not_executed=True,library_changed=False)
                return result
            return self.service.run_one(cancel,queue=queue)
        def clear():
            self.active_tokens.discard(token);self.flights.pop(token,None)
        def done(result):
            clear()
            if self.closed:return
            executed=result and not result.get('not_executed')
            if executed:
                if result.get('state') in ('blocked','failed'):self.failed_jobs.add(result['id'])
                elif result.get('state')=='done':self.failed_jobs.discard(result['id'])
                # Bound diagnostic bookkeeping; persistent job history stays in SQLite.
                while len(self.failed_jobs)>200:self.failed_jobs.pop()
                self.last_state='갱신 대기' if result.get('state') in ('queued','retry') else '준비됨'
                self.changed.emit()
                if result.get('library_changed'):self.library_changed.emit()
            if identity and not self.paused and result and result.get('state') in ('queued','retry'):
                if identity not in self.requests[queue]:self.requests[queue].append(identity)
            self.publish_state()
            # Successful manual page continuations can continue immediately. Retry
            # and source-busy checks wait for the timer instead of spinning.
            if identity and executed and result.get('state') in ('queued','done') and self.requests[queue]:QTimer.singleShot(0,self.tick)
        def error(message):
            clear();self.last_state='일부 출처 확인 필요';self.publish_state();self.error.emit(message)
        tasks.submit(work,done,error)

    def resume(self):self.paused=False;self.publish_state();self.tick()
    def pause(self):
        self.paused=True
        self.requests['network'].clear();self.requests['analysis'].clear()
        for flight in self.flights.values():flight['cancel'].set()
        self.publish_state()
    def stop(self):
        if self.closed:return
        self.closed=True;self.timer.stop();self.pause()
        self.service.on_job_started=None
        self.tasks.stop();self.analysis_tasks.stop()
        self.flights.clear();self.active_tokens.clear()
