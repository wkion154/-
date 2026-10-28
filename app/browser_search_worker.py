"""Windows desktop browser adapter. Never runs against blocked cloud destinations."""
import json,platform,sys,time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser


class BrowserSearchFailure(RuntimeError):
    def __init__(self,message,*,stage,blocked=True,status=None,url=None):
        super().__init__(message)
        self.stage,self.blocked,self.status,self.url=stage,blocked,status,url


def failure_payload(error):
    return dict(error=str(error),blocked=getattr(error,'blocked',True),
                status=getattr(error,'status',None),stage=getattr(error,'stage','browser'),
                page_url=getattr(error,'url',None))


def remote_failure(data):
    from .providers.http import RemoteError
    error=RemoteError(data['error'],blocked=data.get('blocked',True),status=data.get('status'))
    error.stage=data.get('stage','browser');error.page_url=data.get('page_url')
    return error


def launch_browser(chromium,show_browser=False):
    for channel in ('msedge','chrome'):
        try:
            return chromium.launch(channel=channel,headless=show_browser is not True,chromium_sandbox=True)
        except Exception:
            pass
    raise RuntimeError('Windows Edge/Chrome 검색 브라우저를 시작하지 못했습니다. 검색 출처의 [검색 브라우저 표시]에서 표시 모드로 진단할 수 있습니다.')


def run(request):
    if platform.system()!='Windows':raise RuntimeError('이 브라우저 검색은 Windows 사용자 PC에서 실행해야 합니다.')
    from playwright.sync_api import sync_playwright
    from .providers.kpopping_scroll import capture_replayed_batch,artist_filter,selected_artist_pattern,require_visible_artist
    import threading
    url=request['url'];host=urlsplit(url).hostname
    if host!='kpopping.com' or urlsplit(url).scheme!='https':raise ValueError('등록되지 않은 브라우저 검색 사이트입니다.')
    with sync_playwright() as playwright:
        browser=launch_browser(playwright.chromium,request.get('show_browser') is True)
        try:
            context=browser.new_context(accept_downloads=False,service_workers='block')
            response=context.request.get('https://kpopping.com/robots.txt',timeout=15000)
            if response.status not in (200,404,410):raise BrowserSearchFailure('robots 확인 실패: '+str(response.status),stage='robots',status=response.status,url='https://kpopping.com/robots.txt')
            body=response.text() if response.status==200 else 'User-agent: *\nAllow: /'
            if body.lstrip().startswith('<'):raise RuntimeError('robots 대신 접근 확인 화면이 반환됐습니다.')
            rules=RobotFileParser();rules.parse(body.splitlines())
            if not rules.can_fetch('K-IDOL-ARCHIVE',url):raise RuntimeError('이 경로의 자동 접근은 robots에서 허용되지 않습니다.')
            page=context.new_page();page.set_default_timeout(6000)
            response=page.goto(url,wait_until='domcontentloaded',timeout=25000)
            if response and response.status>=400:raise BrowserSearchFailure('사이트 접근 오류: '+str(response.status)+' · '+page.url+' · 사이트가 요청을 거부해 검색을 중단했습니다.',stage='listing',status=response.status,url=page.url)
            page.wait_for_timeout(1500)
            title=page.title().lower()
            if any(t in title for t in ('captcha','just a moment','access denied')):raise RuntimeError('접근 확인 화면입니다. 우회하지 않습니다.')
            if page.locator('input[type=password],iframe[src*="captcha"],iframe[src*="challenge"]').count():raise RuntimeError('로그인/접근 확인 화면입니다.')
            if not artist_filter(page.url)[0]:
                fields=page.locator('input[role=combobox],input[type=search],input[placeholder*="idol" i],input[placeholder*="search" i]')
                visible=next((fields.nth(i) for i in range(min(fields.count(),12)) if fields.nth(i).is_visible()),None)
                if visible is None:raise RuntimeError('인물 검색 입력란을 찾지 못했습니다. 알려진 인물 페이지 또는 사이트 구조 확인이 필요합니다.')
                visible.fill(request['query']);page.wait_for_timeout(1200)
                options=page.get_by_text(request['query'],exact=True)
                chosen=next((options.nth(i) for i in range(min(options.count(),12)) if options.nth(i).is_visible()),None)
                if chosen is None:raise BrowserSearchFailure('정확히 대응하는 인물/그룹 선택지를 확인하지 못했습니다.',stage='entity_selection',blocked=False,url=page.url)
                chosen.click();page.wait_for_timeout(1000)
            if not artist_filter(page.url)[0]:raise RuntimeError('검색 인물 필터 적용을 확인하지 못했습니다.')
            try:
                page.get_by_role('button',name=selected_artist_pattern(page.url)).first.wait_for(state='visible',timeout=15000)
            except Exception as error:
                raise RuntimeError('사이트 화면의 인물 필터 적용을 확인하지 못했습니다. 페이지 로딩 또는 검색 구조를 확인하세요.') from error
            require_visible_artist(page)
            if not rules.can_fetch('K-IDOL-ARCHIVE',page.url):raise RuntimeError('선택된 검색 경로의 자동 접근이 금지됐습니다.')
            # Capture intermediate DOMs too: virtualized cards can disappear
            # before the final replay scroll position is reached.
            batch=request.get('batch',0)
            items,cursor=capture_replayed_batch(page,request['query'],batch,request.get('scroll_cursor'),threading.Event())
            return dict(page_url=page.url,title=page.title(),items=[dict(url=i.url,logical_key=i.logical_key,title=i.title,role=i.role,provenance=i.provenance) for i in items],scroll_cursor=cursor,next_batch=batch+1 if batch<20 else None,exhaustion_verified=False,filter_verified=True,automated_artist_selection=not bool(request.get('known_artist_page')))
        finally:browser.close()

if __name__=='__main__':
    try:
        request=json.loads(Path(sys.argv[1]).read_text('utf-8'));result=run(request)
        Path(sys.argv[2]).write_text(json.dumps(dict(result=result),ensure_ascii=False),'utf-8')
    except Exception as error:
        Path(sys.argv[2]).write_text(json.dumps(failure_payload(error),ensure_ascii=False),'utf-8');sys.exit(1)
