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
    def submit(self, function, done, error):
        job = Job(function)
        self.jobs.add(job)
        job.signals.done.connect(done)
        job.signals.error.connect(error)
        job.signals.finished.connect(lambda: self.jobs.discard(job))
        self.pool.start(job)
        return job
    def stop(self):
        for job in self.jobs:
            job.cancelled.set()
        self.pool.clear()
        self.pool.waitForDone()
        self.jobs.clear()
