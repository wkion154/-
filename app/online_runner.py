"""One low-priority persistent online job at a time; no gallery decoder pool sharing."""
import threading
import time
from PySide6.QtCore import QObject,QTimer,Signal
from .workers import Tasks

class OnlineRunner(QObject):
    changed=Signal()
    library_changed=Signal()
    error=Signal(str)
    def __init__(self,service,settings,parent=None):
        super().__init__(parent)
        self.service,self.settings=service,settings
        self.tasks=Tasks(self,1)
        self.cancelled=threading.Event()
        self.running=False
        self.closed=False
        self.last_schedule=0
        service.repo.recover()
        self.timer=QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def tick(self,manual=False):
        if self.closed or self.running:return
        if not manual and not self.settings.values.get('online_enabled',True):return
        self.running=True
        self.cancelled.clear()
        def run(_):
            # Source checks and queue writes stay off the UI thread too.
            if self.service.repo.reviewed() and time.monotonic()-self.last_schedule>=60:
                self.service.repo.schedule();self.last_schedule=time.monotonic()
            return self.service.run_one(self.cancelled)
        def done(result):
            self.running=False
            if result:
                self.changed.emit()
                if result.get('library_changed'):self.library_changed.emit()
        def error(message):
            self.running=False
            self.error.emit(message)
        self.tasks.submit(run,done,error)

    def pause(self):self.cancelled.set()

    def stop(self):
        self.closed=True
        self.timer.stop()
        self.cancelled.set()
        self.tasks.stop()
