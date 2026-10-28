"""Windows-only visible-browser connectivity/image discovery diagnostic.
No login, bypass, original-image download or application-completion claim.
"""
import datetime,html,json,platform,re,sys,traceback
from pathlib import Path
from urllib.parse import urlsplit


def safe_image(url):
    try:
        p=urlsplit(url)
        return p.scheme=='https' and bool(p.hostname) and not p.username and not p.password and len(url)<8192
    except ValueError:return False


def candidates(rows):
    output=[];seen=set()
    for row in rows:
        for url in (row.get('currentSrc'),row.get('src')):
            if not safe_image(url or '') or url in seen:continue
            seen.add(url);output.append(dict(url=url,width=row.get('width',0),height=row.get('height',0),alt=str(row.get('alt',''))[:300],status='browser-loaded candidate; not verified original or identity'))
            if len(output)>=100:return output
    return output


def write_report(folder,report):
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'search-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['<!doctype html><meta charset="utf-8"><title>K-IDOL 검색 진단</title><h1>검색 진단 결과</h1>','<p>원본 수집·인물 일치·사용권·앱 통합 완료를 증명하는 결과가 아닙니다.</p>','<pre>'+html.escape(json.dumps({k:v for k,v in report.items() if k!='images'},ensure_ascii=False,indent=2))+'</pre>','<h2>브라우저에 로드된 이미지 후보</h2>']
    for row in report.get('images',[]):
        lines.append('<p>'+html.escape(str(row['width'])+' × '+str(row['height'])+' '+row['alt'])+'<br><a rel="noreferrer" href="'+html.escape(row['url'],quote=True)+'">이미지 주소 열기</a></p>')
    (folder/'RESULT.html').write_text('\n'.join(lines),encoding='utf-8')


def run():
    if platform.system()!='Windows':raise RuntimeError('Windows PC에서만 실행하세요. 클라우드의 네트워크 제한 우회용 도구가 아닙니다.')
    from playwright.sync_api import sync_playwright,TimeoutError as BrowserTimeout
    query=input('검색할 이름 [장원영]: ').strip() or '장원영'
    folder=Path(__file__).resolve().parent/'results'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    report=dict(query=query,source='Kpopping',platform=platform.platform(),stage1_complete=False,images=[],events=[])
    browser=None
    try:
        with sync_playwright() as p:
            for channel in ('msedge','chrome'):
                try:browser=p.chromium.launch(channel=channel,headless=False,chromium_sandbox=True);report['browser']=channel;break
                except Exception:pass
            if browser is None:raise RuntimeError('Microsoft Edge 또는 Google Chrome을 설치하세요. 브라우저 다운로드/인증 우회는 자동 수행하지 않습니다.')
            context=browser.new_context(accept_downloads=False,service_workers='block')
            page=context.new_page();page.set_default_timeout(5000)
            # First inspect public robots information. No permission is inferred from HTTP 200.
            try:
                response=context.request.get('https://kpopping.com/robots.txt',timeout=15000)
                report['robots_status']=response.status
                body=response.text() if response.status==200 else ''
                if response.status in (401,403,429):raise RuntimeError('robots 접근 차단/제한: '+str(response.status))
                if response.status==200:
                    if body.lstrip().startswith('<'):raise RuntimeError('robots 대신 접근 확인 페이지가 반환됐습니다.')
                    from urllib.robotparser import RobotFileParser
                    rules=RobotFileParser();rules.parse(body.splitlines())
                    if not rules.can_fetch('K-IDOL-ARCHIVE','https://kpopping.com/'):raise RuntimeError('robots가 자동 접근을 허용하지 않습니다.')
                elif response.status not in (404,410):raise RuntimeError('robots 정책 확인 실패')
            except Exception as error:
                report['state']='policy_or_access_blocked';report['error']=str(error);return report,folder
            response=page.goto('https://kpopping.com/',wait_until='domcontentloaded',timeout=25000)
            report['http_status']=response.status if response else None
            if response and response.status in (401,403,429):raise RuntimeError('사이트 접근 차단/제한: '+str(response.status))
            def blocked():
                title=page.title().lower()
                return bool(re.search(r'captcha|just a moment|access denied|verify.*human',title)) or page.locator('input[type=password],iframe[src*="captcha"],iframe[src*="challenge"]').count()>0
            if blocked():raise RuntimeError('접근 확인/로그인/CAPTCHA 화면입니다. 우회하지 않고 중단합니다.')
            fields=page.locator('input[type="search"],input[placeholder*="Search" i],input[name="search"],input[name="q"]')
            report['search_submitted']=False
            if fields.count() and fields.first.is_visible():
                fields.first.fill(query);fields.first.press('Enter');report['search_submitted']=True
            print('\n브라우저가 열렸습니다. 자동 검색이 되지 않았다면 사이트의 검색창에서 직접 이름을 검색하세요.')
            print('로그인이나 CAPTCHA가 나오면 진행하지 말고 브라우저를 닫으세요.')
            print('원하는 검색 결과 화면이 보이면 이 창으로 돌아와 Enter를 누르세요. 스크롤도 직접 해볼 수 있습니다.')
            input()
            if blocked():raise RuntimeError('접근 확인/로그인 화면으로 이동했습니다. 이미지 추출을 중단합니다.')
            if urlsplit(page.url).hostname not in ('kpopping.com','www.kpopping.com'):raise RuntimeError('허용된 진단 사이트 밖으로 이동했습니다.')
            from urllib.robotparser import RobotFileParser
            if body:
                rules=RobotFileParser();rules.parse(body.splitlines())
                if not rules.can_fetch('K-IDOL-ARCHIVE',page.url):raise RuntimeError('현재 검색 경로의 robots가 자동 접근을 허용하지 않습니다.')
            report.update(page_url=page.url,page_title=page.title(),images=candidates(page.locator('img').evaluate_all('(images)=>images.slice(0,200).map(i=>({src:i.src,currentSrc:i.currentSrc,width:i.naturalWidth,height:i.naturalHeight,alt:i.alt}))')),state='diagnostic_completed')
            report['image_count']=len(report['images'])
            print('이미지 후보',report['image_count'],'개. 이 수치는 인물 검색 정확도나 원본 확인 결과가 아닙니다.')
            return report,folder
    except Exception as error:report.update(state='error',error=str(error));return report,folder
    finally:
        if browser:
            try:browser.close()
            except Exception:pass

if __name__=='__main__':
    try:
        report,folder=run();write_report(folder,report)
        print('\n결과 폴더:',folder)
        print('search-report.json 파일을 이 대화에 첨부해 주세요. 쿠키·비밀번호·로그인 프로필은 저장하지 않습니다.')
        sys.exit(0 if report.get('state')=='diagnostic_completed' else 1)
    except Exception as error:
        print('실행 실패:',error);sys.exit(1)
