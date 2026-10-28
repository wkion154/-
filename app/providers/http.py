import email.utils
import json
import ssl
import threading
import time
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

HOSTS = frozenset({'commons.wikimedia.org','upload.wikimedia.org','www.wikidata.org','query.wikidata.org',
                   'www.mediawiki.org','foundation.wikimedia.org','doc.wikimedia.org'})

class Cancelled(Exception):
    pass

class RemoteError(Exception):
    def __init__(self,message,retryable=False,retry_after=None,blocked=False):
        super().__init__(message)
        self.retryable,self.retry_after,self.blocked=retryable,retry_after,blocked


def validate_url(url):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None,443):
        raise RemoteError('허용되지 않은 제공처 주소입니다.',blocked=True)
    return url

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

class HttpClient:
    """Proxy/CA settings inherited; serial API requests, no auth or TLS bypass."""
    def __init__(self,interval=1.1,timeout=12):
        self.interval,self.timeout=interval,timeout
        self.last={}
        self.lock=threading.Lock()
        self.opener=build_opener(HTTPSHandler(context=ssl.create_default_context()),SafeRedirect())

    def open(self,url,cancelled,accept='application/json'):
        validate_url(url)
        host=urlsplit(url).hostname
        with self.lock:
            wait=max(0,self.interval-(time.monotonic()-self.last.get(host,0)))
            if cancelled.wait(wait):raise Cancelled()
            self.last[host]=time.monotonic()
        if cancelled.is_set():raise Cancelled()
        request=Request(url,headers={'User-Agent':'K-IDOL-ARCHIVE/0.3.0 (desktop photo archive)', 'Accept':accept})
        try:
            return self.opener.open(request,timeout=self.timeout)
        except HTTPError as error:
            status=error.code
            delay=None
            raw=error.headers.get('Retry-After')
            if raw:
                try:delay=max(0,int(raw))
                except ValueError:
                    try:delay=max(0,email.utils.parsedate_to_datetime(raw).timestamp()-time.time())
                    except (TypeError,ValueError,OverflowError):pass
            error.close()
            raise RemoteError(f'제공처 HTTP {status}',retryable=status in (408,429,500,502,503,504),retry_after=delay,blocked=status in (401,403)) from None
        except (URLError,TimeoutError,OSError) as error:
            message=str(error)
            blocked='403' in message and 'Tunnel' in message
            raise RemoteError('네트워크 연결을 확인하세요: '+message,retryable=not blocked,blocked=blocked) from None

    def json(self,base,params,cancelled,max_bytes=8*1024*1024):
        url=base+'?'+urlencode(params)
        with self.open(url,cancelled) as response:
            raw=response.read(max_bytes+1)
        if len(raw)>max_bytes:raise RemoteError('API 응답 크기 한도를 초과했습니다.')
        if cancelled.is_set():raise Cancelled()
        try:data=json.loads(raw)
        except (ValueError,UnicodeError):raise RemoteError('제공처 JSON 응답을 읽지 못했습니다.',retryable=True) from None
        if 'error' in data:
            code=data['error'].get('code','unknown')
            raise RemoteError('API 오류: '+code,retryable=code in ('maxlag','ratelimited','readonly'))
        return data
