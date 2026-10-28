class Cancelled(Exception):
    pass

class RemoteError(Exception):
    def __init__(self,message,retryable=False,retry_after=None,blocked=False,status=None):
        super().__init__(message)
        self.retryable,self.retry_after,self.blocked=retryable,retry_after,blocked
        self.status=status
