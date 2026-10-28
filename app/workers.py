"""GUI-owned scheduling; obsolete work never accumulates in Qt's runnable queue."""
from collections import deque
import logging
import threading
from PySide6.QtCore import QObject, QRunnable, Signal, QThreadPool

class Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    finished = Signal()

class Job(QRunnable):
    def __init__(self, function, key=None):
        super().__init__()
        self.function = function
        self.key = key
        self.signals = Signals()
        self.cancelled = threading.Event()
        self.disposed = False
    def run(self):
        try:
            if self.cancelled.is_set():return
            value = self.function(self.cancelled)
            if not self.cancelled.is_set():self.signals.done.emit(value)
        except Exception as error:
            if not self.cancelled.is_set():
                logging.exception('Background task failed')
                self.signals.error.emit(str(error))
        finally:
            self.signals.finished.emit()

class Tasks(QObject):
    def __init__(self, parent=None, threads=2):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(threads)
        self.jobs = set()
        self.active = set()
        self.pending = deque()
        self.stopped = False

    def _dispose(self,job):
        if job.disposed:return
        job.disposed=True
        # Break signal/closure reference cycles, including jobs discarded before
        # Qt ever runs them. Active jobs are disposed only after completion.
        job.signals.done.disconnect();job.signals.error.disconnect();job.signals.finished.disconnect()

    def _prune(self):
        retained = deque()
        for job in self.pending:
            if job.cancelled.is_set():self._dispose(job);self.jobs.discard(job)
            else:retained.append(job)
        self.pending = retained

    def _dispatch(self):
        if self.stopped:return
        self._prune()
        while self.pending and len(self.active)<self.pool.maxThreadCount():
            job=self.pending.popleft()
            self.active.add(job)
            self.pool.start(job)

    def _finished(self,job):
        self._dispose(job)
        self.active.discard(job);self.jobs.discard(job)
        self._dispatch()

    def submit(self, function, done, error, key=None):
        if self.stopped:raise RuntimeError('종료된 작업 관리자입니다.')
        if key is not None:
            for old in self.jobs:
                if old.key==key:old.cancelled.set()
        self._prune()
        job = Job(function,key)
        self.jobs.add(job)
        job.signals.done.connect(lambda value: done(value) if not self.stopped and not job.cancelled.is_set() else None)
        job.signals.error.connect(lambda message: error(message) if not self.stopped and not job.cancelled.is_set() else None)
        job.signals.finished.connect(lambda: self._finished(job))
        self.pending.append(job)
        self._dispatch()
        return job

    def stop(self):
        if self.stopped:return
        self.stopped = True
        for job in self.jobs:job.cancelled.set()
        self.pending.clear()
        # Only active jobs were handed to Qt; no queued auto-deleted runnable is
        # removed with tryTake(), avoiding QRunnable's documented ABA hazard.
        self.pool.waitForDone()
        for job in self.jobs:self._dispose(job)
        self.jobs.clear();self.active.clear()
