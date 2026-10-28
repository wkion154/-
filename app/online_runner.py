"""Independent bounded network/analysis workers, with targeted manual requests."""
from collections import deque
import threading
import time
from PySide6.QtCore import QObject,QTimer,Signal
from .workers import Tasks

class OnlineRunner(QObject):
    changed=Signal()
    library_changed=Signal()
    error=Signal(str)
    state_changed=Signal(str)
    started=Signal(str)
    def __init__(self,service,settings,parent=None):
        super().__init__(parent)
        self.service,self.settings=service,settings
        self.tasks=Tasks(self,1)
        self.analysis_tasks=Tasks(self,1)
        self.cancelled=threading.Event();self.analysis_cancelled=threading.Event()
        self.running=False;self.analysis_running=False
        self.closed=False;self.paused=False;self.last_schedule=0;self.failed_jobs=set();self.last_state='준비됨'
        self.active_queues=set()
        self.started.connect(self.mark_active)
        self.service.on_job_started=lambda queue:self.started.emit(queue)
        self.requests={'network':deque(),'analysis':deque()}
        service.repo.recover()
        self.timer=QTimer(self);self.timer.setInterval(1500);self.timer.timeout.connect(self.tick);self.timer.start()

    def request(self,identity,queue='network'):
        if self.closed:return
        if identity not in self.requests[queue]:self.requests[queue].append(identity)
        self.tick()

    def tick(self,manual=False):
        if self.closed:return
        automatic=(self.settings.values.get('online_enabled',True) and not self.paused) or manual
        for queue in ('network','analysis'):
            if (self.running if queue=='network' else self.analysis_running):continue
            identity=self.requests[queue].popleft() if self.requests[queue] else None
            if not automatic and not identity:continue
            self.launch(queue,identity,automatic)

    def mark_active(self,queue):
        if self.closed or not (self.running if queue=='network' else self.analysis_running):return
        self.active_queues.add(queue);self.publish_state()

    def publish_state(self):
        if {'network','analysis'}.issubset(self.active_queues):message='동기화·검사 중'
        elif 'network' in self.active_queues:message='동기화 중'
        elif 'analysis' in self.active_queues:message='이미지 검사 중'
        elif self.paused:message='갱신 일시 중지'
        elif self.failed_jobs:message='일부 출처 확인 필요'
        else:message=self.last_state
        self.state_changed.emit(message)

    def launch(self,queue,identity,automatic):
        if queue=='network':self.running=True;tasks=self.tasks;cancel=self.cancelled
        else:self.analysis_running=True;tasks=self.analysis_tasks;cancel=self.analysis_cancelled
        cancel.clear()
        self.publish_state()
        def work(_):
            if queue=='network' and automatic and time.monotonic()-self.last_schedule>=60:
                ready=self.service.ready() if hasattr(self.service,'ready') else self.service.repo.reviewed()
                if ready:self.service.repo.schedule()
                if hasattr(self.service,'web'):self.service.web.schedule(self.service.repo)
                self.last_schedule=time.monotonic()
            if identity:
                result=self.service.run_one(cancel,queue=queue,job_id=identity)
                if result is None:
                    state=self.service.repo.job_state(identity)
                    return dict(id=identity,state=state,not_executed=True,library_changed=False)
                return result
            return self.service.run_one(cancel,queue=queue)
        def done(result):
            self.active_queues.discard(queue)
            if queue=='network':self.running=False
            else:self.analysis_running=False
            if result and not result.get('not_executed'):
                if result.get('state') in ('blocked','failed'):self.failed_jobs.add(result['id'])
                elif result.get('state')=='done':self.failed_jobs.discard(result['id'])
                self.last_state='갱신 대기' if result.get('state') in ('queued','retry') else '준비됨'
                self.changed.emit()
                if result.get('library_changed'):self.library_changed.emit()
            # Manual continuations/retries remain authorized, but never start an
            # unrelated network job when the user only requested local analysis.
            if identity and not self.paused:
                state=result.get('state') if result else None
                if state in ('queued','retry') or result is None:
                    self.requests[queue].append(identity)
            self.publish_state()
        def error(message):
            self.active_queues.discard(queue)
            if queue=='network':self.running=False
            else:self.analysis_running=False
            self.last_state='일부 출처 확인 필요'
            self.publish_state()
            self.error.emit(message)
        tasks.submit(work,done,error)

    def resume(self):self.paused=False;self.publish_state()

    def pause(self):
        self.paused=True
        self.requests['network'].clear();self.requests['analysis'].clear()
        self.cancelled.set();self.analysis_cancelled.set()
        self.publish_state()

    def stop(self):
        self.closed=True;self.timer.stop();self.pause()
        self.service.on_job_started=None
        self.tasks.stop();self.analysis_tasks.stop()
