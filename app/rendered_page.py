"""Optional sandboxed Chromium DOM discovery; no browser network fallback.
Network resources are mediated by the reviewed application client. No persistent
profile, login, CAPTCHA interaction, image capture or screenshot-as-original path.
"""
import asyncio
import shutil
import time
from .providers.http import RemoteError,Cancelled,validate_url


async def _render(html,url,spec,fetch,cancelled):
    from playwright.async_api import async_playwright
    executable=shutil.which('chromium') or shutil.which('google-chrome')
    budget={'requests':0,'bytes':len(html.encode()),'document_served':False}
    resource_slot=asyncio.Semaphore(1)
    closing=set()
    playwright=await async_playwright().start()
    browser=None
    try:
        options=dict(headless=True,chromium_sandbox=True,args=[
            '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
            '--disable-gpu','--disable-webgl','--js-flags=--max-old-space-size=128'])
        if executable:options['executable_path']=executable
        browser=await playwright.chromium.launch(**options)
        context=await browser.new_context(offline=True,service_workers='block',accept_downloads=False,viewport={'width':1280,'height':900})
        context.set_default_timeout(5000)
        def block_socket(socket):
            # A routed socket never connects externally unless connect_to_server()
            # is called (we never do). Close only after the handler returns: calling
            # synchronous close in the route handler deadlocks before ensureOpened.
            async def close_after_interception():
                await asyncio.sleep(.01)
                try:await asyncio.wait_for(socket.close(code=1008,reason='Network disabled for DOM discovery'),2)
                except Exception:pass
            task=asyncio.create_task(close_after_interception())
            closing.add(task);task.add_done_callback(closing.discard)
        await context.route_web_socket('**/*',block_socket)
        async def route(request_route):
            request=request_route.request
            if cancelled.is_set() or request.method not in ('GET','HEAD'):
                await request_route.abort();return
            try:validate_url(request.url,spec.domains)
            except (RemoteError,ValueError):await request_route.abort();return
            if request.resource_type=='document' and not budget['document_served'] and request.url.rstrip('/')==url.rstrip('/'):
                budget['document_served']=True
                await request_route.fulfill(status=200,content_type='text/html; charset=utf-8',body=html);return
            allowed={'script','stylesheet'}
            if spec.render_allow_xhr:allowed|={'xhr','fetch'}
            if request.resource_type not in allowed or budget['requests']>=20:
                await request_route.abort();return
            budget['requests']+=1
            try:
                async with resource_slot:
                    if cancelled.is_set():raise Cancelled()
                    body,headers=await asyncio.to_thread(fetch,request.url)
                budget['bytes']+=len(body)
                if budget['bytes']>8*1024*1024:raise RemoteError('렌더링 리소스 한도를 초과했습니다.')
                content_type=next((v for k,v in headers.items() if k.lower()=='content-type'),'text/plain')
                await request_route.fulfill(status=200,content_type=content_type,body=body)
            except Exception:
                await request_route.abort()
        await context.route('**/*',route)
        page=await context.new_page()
        await page.goto(url,wait_until='domcontentloaded',timeout=15000)
        await page.wait_for_timeout(350)
        if cancelled.is_set():raise Cancelled()
        await page.evaluate("""() => {for (const image of document.images) {
            const bounds=image.getBoundingClientRect();const style=getComputedStyle(image);
            image.setAttribute('data-kidol-visible',String(bounds.width>0 && bounds.height>0 && style.display!=='none' && style.visibility!=='hidden'));
        }}""")
        return await page.content()
    finally:
        for task in list(closing):task.cancel()
        if browser:
            try:await asyncio.wait_for(browser.close(),3)
            except Exception:pass
        try:await asyncio.wait_for(playwright.stop(),3)
        except Exception:pass


def render_page(html,url,spec,fetch,cancelled):
    try:import playwright.async_api
    except ImportError:raise RemoteError('선택적 브라우저 구성요소가 없습니다. 공개 HTML 수집은 계속 사용할 수 있습니다.',blocked=True) from None
    async def bounded():return await asyncio.wait_for(_render(html,url,spec,fetch,cancelled),18)
    try:return asyncio.run(bounded())
    except Cancelled:raise
    except Exception as error:
        # No sandbox-disabled or unmediated fallback.
        raise RemoteError('보안 샌드박스 브라우저 렌더링에 실패했습니다: '+str(error).splitlines()[0] if str(error) else '브라우저 렌더링 제한 시간을 초과했습니다.',blocked=True) from None
