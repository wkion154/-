import logging
import threading
from PySide6.QtCore import QObject, QRunnable, Signal, QThreadPool

class Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    finished = Signal()

class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = Signals()
        self.cancelled = threading.Event()
    def run(self):
        try:
            if self.cancelled.is_set():
                return
            value = self.function(self.cancelled)
            if not self.cancelled.is_set():
                self.signals.done.emit(value)
        except Exception as error:
            logging.exception('Background task failed')
            if not self.cancelled.is_set():
                self.signals.error.emit(str(error))
        finally:
            self.signals.finished.emit()

class Tasks(QObject):
    def __init__(self, parent=None, threads=2):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(threads)
        self.jobs = set()
        self.stopped = False
    def submit(self, function, done, error):
        if self.stopped:
            raise RuntimeError('종료된 작업 관리자입니다.')
        job = Job(function)
        self.jobs.add(job)
        job.signals.done.connect(lambda value: done(value) if not self.stopped and not job.cancelled.is_set() else None)
        job.signals.error.connect(lambda message: error(message) if not self.stopped and not job.cancelled.is_set() else None)
        job.signals.finished.connect(lambda: self.jobs.discard(job))
        self.pool.start(job)
        return job
    def stop(self):
        if self.stopped:return
        self.stopped = True
        for job in self.jobs:
            job.cancelled.set()
        self.pool.clear()
        self.pool.waitForDone()
        self.jobs.clear()
