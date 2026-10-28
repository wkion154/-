"""One application owner per data directory, including queue crash recovery."""
from pathlib import Path
from PySide6.QtCore import QLockFile


class LibraryInstance:
    def __init__(self,root):
        root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
        self.lock=QLockFile(str(root/'application.lock'))
        # Do not steal a healthy process's lock just because it is old. Qt still
        # checks recorded process identity when detecting a crashed owner.
        self.lock.setStaleLockTime(0)
    def acquire(self):
        if not self.lock.tryLock(0):
            raise RuntimeError('이 데이터 폴더가 다른 K-IDOL ARCHIVE에서 열려 있거나 잠금을 만들 수 없습니다. 실행 중인 창과 폴더 쓰기 권한을 확인하세요.')
        return self
    def release(self):self.lock.unlock()
    def __enter__(self):return self.acquire()
    def __exit__(self,*_):self.release()
