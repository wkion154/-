"""App adapter for the Windows browser worker, sharing existing web image records."""
import json,platform,subprocess,sys,tempfile,time
from pathlib import Path
from .http import RemoteError,Cancelled
from .kpopping_scroll import artist_filter
from ..page_discovery import ParsedPage,ImageCandidate
from .search_previews import allow_reviewed_previews

# Exact public artist page observed in the user's Windows diagnostic, not a guessed ID.
OBSERVED_PAGES={'장원영':'https://kpopping.com/kpics?idol=4ce3bdec-4bb1-4364-b84f-f3684cfc62d9&idolName=Jang%20Wonyoung','jang wonyoung':'https://kpopping.com/kpics?idol=4ce3bdec-4bb1-4364-b84f-f3684cfc62d9&idolName=Jang%20Wonyoung'}
SPEC=dict(id='kpopping-browser',name='Kpopping · Windows 브라우저 실험',category='fan',adapter='html',domains=['kpopping.com','cdn.kpopping.com'],policy_urls=['https://kpopping.com/robots.txt'])


class KpoppingBrowserProvider:
    name='kpopping-browser'
    identity_search=True
    def __init__(self,online):self.online=online;online.web.repo.save_spec(SPEC)
    def search_images(self,query,cursor,cancelled):
        if self.online.settings.values.get('automatic_search',True) is True:
            self.online.settings.values['kpopping_provided_preview']=True
        if platform.system()!='Windows':raise RemoteError('Windows 사용자 PC의 브라우저 검색 경로입니다.',blocked=True)
        repo=self.online.web.repo;repo.require(self.name,'metadata_search')
        term=query.strip().casefold();known=next((url for name,url in OBSERVED_PAGES.items() if term==name or term.startswith(name+' ')),None);url=known or 'https://kpopping.com/kpics'
        try:
            result=self._worker(dict(query=query,url=url,known_artist_page=bool(known),batch=(cursor or {}).get('batch',0),scroll_cursor=(cursor or {}).get('scroll_cursor'),show_browser=False if self.online.settings.values.get('automatic_search',True) is True else self.online.settings.values.get('kpopping_show_browser') is True),cancelled)
        except Exception as error:
            self._audit(dict(query=query,state='error',error=str(error),stage1_complete=False));raise
        self._audit(dict(query=query,state='browser_candidates_received',page_url=result['page_url'],candidate_count=len(result['items']),filter_verified=result.get('filter_verified') is True,original_verified=False,stage1_complete=False))
        if cancelled.is_set():raise Cancelled()
        if result.get('filter_verified') is not True:raise RemoteError('브라우저가 실제 화면의 인물 필터를 확인하지 못했습니다.',blocked=True)
        if not artist_filter(result['page_url'])[0]:raise RemoteError('검색 인물 필터가 없습니다.',blocked=True)
        if known and artist_filter(result['page_url'])!=artist_filter(known):raise RemoteError('검색 인물 필터가 바뀌었습니다.',blocked=True)
        page_id=repo.add_page(self.name,result['page_url'])
        parsed=ParsedPage(result['title'],[ImageCandidate(i['url'],i['logical_key'],i['title'],'thumbnail',None,None,i['provenance']) for i in result['items']],metadata=dict(query=query,search_managed=True,original_verified=False,person_confirmed=False))
        # Worker output URLs are still checked against the registered source domain.
        from .http import validate_url
        for item in parsed.candidates:validate_url(item.url,SPEC['domains'])
        repo.require(self.name,'metadata_search');repo.index_page(repo.page(page_id),parsed)
        rows=repo.images_for_urls(page_id,[item.url for item in parsed.candidates])
        allow_reviewed_previews(repo,self.name,rows)
        if self.online.settings.values.get('kpopping_provided_preview') is True:
            allow_reviewed_previews(repo,self.name,rows,operation='original_download')
        next_cursor=dict(batch=result['next_batch']) if result['next_batch'] is not None else None
        if next_cursor is not None and result.get('scroll_cursor'):next_cursor['scroll_cursor']=result['scroll_cursor']
        return ['web:'+row['id'] for row in rows],next_cursor
    def _audit(self,data):
        folder=self.online.settings.root/'search-reports';folder.mkdir(parents=True,exist_ok=True)
        path=folder/('kpopping-'+str(time.time_ns())+'.json');path.write_text(json.dumps(data,ensure_ascii=False,indent=2),'utf-8')

    def _worker(self,request,cancelled):
        with tempfile.TemporaryDirectory(prefix='kidol-browser-') as directory:
            root=Path(directory);input_path=root/'request.json';output_path=root/'result.json'
            input_path.write_text(json.dumps(request),'utf-8');process=None
            try:
                flags=getattr(subprocess,'CREATE_NO_WINDOW',0) if platform.system()=='Windows' else 0
                process=subprocess.Popen([sys.executable,'-m','app.browser_search_worker',str(input_path),str(output_path)],cwd=Path(__file__).resolve().parents[2],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
                start=time.monotonic()
                while process.poll() is None:
                    if cancelled.wait(.1):raise Cancelled()
                    if time.monotonic()-start>90:raise RemoteError('브라우저 검색 시간 제한을 초과했습니다.')
                if not output_path.is_file():raise RemoteError('브라우저 검색 실행에 실패했습니다. 선택 브라우저 의존성을 확인하세요.')
                data=json.loads(output_path.read_text('utf-8'))
                if 'error' in data:
                    from ..browser_search_worker import remote_failure
                    raise remote_failure(data)
                return data['result']
            finally:
                if process is not None and process.poll() is None:
                    if platform.system()=='Windows':
                        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5,check=False)
                    else:process.terminate()
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
