import email.utils
import json
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

HOSTS = frozenset({'commons.wikimedia.org','upload.wikimedia.org','www.wikidata.org','query.wikidata.org',
                   'www.mediawiki.org','foundation.wikimedia.org','doc.wikimedia.org'})

from .http_errors import RemoteError,Cancelled
from ..request_pacing import HOST_PACER


def validate_url(url,allowed_hosts=HOSTS,public_only=False):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname not in allowed_hosts or parsed.username or parsed.password or parsed.port not in (None,443):
        raise RemoteError('허용되지 않은 제공처 주소입니다.',blocked=True)
    if public_only:
        host=parsed.hostname
        if host=='localhost' or host.endswith(('.localhost','.local','.internal')):raise RemoteError('공개 인터넷 주소만 사용할 수 있습니다.',blocked=True)
        try:
            addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        except OSError as error:raise RemoteError('도메인 주소를 확인할 수 없습니다.',retryable=True) from error
        if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
            raise RemoteError('사설·로컬 네트워크 주소에는 접근하지 않습니다.',blocked=True)
    return url

class SafeRedirect(HTTPRedirectHandler):
    def __init__(self,allowed_hosts=HOSTS,public_only=False,pacer=HOST_PACER,interval=1.1):
        super().__init__();self.allowed_hosts=allowed_hosts;self.public_only=public_only;self.pacer=pacer;self.interval=interval
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        validate_url(newurl,self.allowed_hosts,self.public_only)
        redirected=super().redirect_request(req,fp,code,msg,headers,newurl)
        if redirected is not None:
            cancelled=getattr(req,'archive_cancelled',threading.Event())
            self.pacer.wait(urlsplit(newurl).hostname,self.interval,cancelled)
            redirected.archive_cancelled=cancelled
        return redirected

class HttpClient:
    """Proxy/CA settings inherited; serial API requests, no auth or TLS bypass."""
    def __init__(self,interval=1.1,timeout=12,allowed_hosts=HOSTS,public_only=False,pacer=HOST_PACER):
        self.interval,self.timeout=interval,timeout
        self.allowed_hosts=frozenset(allowed_hosts)
        self.public_only=public_only
        self.pacer=pacer
        self.opener=build_opener(HTTPSHandler(context=ssl.create_default_context()),SafeRedirect(self.allowed_hosts,public_only,pacer,interval))

    def open(self,url,cancelled,accept='application/json'):
        validate_url(url,self.allowed_hosts,self.public_only)
        host=urlsplit(url).hostname
        self.pacer.wait(host,self.interval,cancelled)
        request=Request(url,headers={'User-Agent':'K-IDOL-ARCHIVE/0.7.1 (desktop photo archive)', 'Accept':accept})
        request.archive_cancelled=cancelled
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
            if delay is not None:self.pacer.cooldown(host,delay)
            error.close()
            raise RemoteError(f'제공처 HTTP {status}',retryable=status in (408,429,500,502,503,504),retry_after=delay,blocked=status in (401,403),status=status) from None
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
