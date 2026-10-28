"""Process-wide per-host pacing across independent source HTTP clients."""
import math
import threading
import time
from .providers.http_errors import RemoteError,Cancelled

class HostPacer:
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.lock=threading.Lock();self.next={}
    def wait(self,host,interval,cancelled):
        while True:
            if cancelled.is_set():raise Cancelled()
            with self.lock:
                now=self.clock();delay=max(0,self.next.get(host,0)-now)
                if delay<=0:
                    self.next[host]=now+interval;return
            # A long server cooldown becomes a durable queued retry, not a worker
            # waiting for minutes while unrelated sources could run.
            if delay>2:raise RemoteError('제공처 요청 간격 대기',retryable=True,retry_after=math.ceil(delay),status=429)
            if cancelled.wait(delay):raise Cancelled()
    def cooldown(self,host,seconds):
        with self.lock:self.next[host]=max(self.next.get(host,0),self.clock()+max(0,seconds))

HOST_PACER=HostPacer()
