"""Official authenticated Naver image search; preview URLs only, no web scraping."""
import hashlib
import json
from html import unescape
import re
from urllib.parse import urlencode
from .http import HttpClient,RemoteError,Cancelled,validate_url
from .search_previews import allow_reviewed_previews
from ..page_discovery import ImageCandidate,ParsedPage,clean_url
from .. import naver_credentials

SPEC=dict(id='naver-image-search',name='네이버 · 공식 이미지 검색 API',category='press',adapter='html',
          domains=['openapi.naver.com','search.naver.com','search.pstatic.net'],
          policy_urls=['https://developers.naver.com/docs/serviceapi/search/image/image.md'])
ENDPOINT='https://openapi.naver.com/v1/search/image.json'


def linked_file(item):
    try:evidence=json.loads(item['provenance'])
    except (TypeError,ValueError,KeyError):return None
    if not isinstance(evidence,list):return None
    urls={clean_url(row.get('linked_image_url',''),'https://search.naver.com/') for row in evidence
          if isinstance(row,dict) and row.get('method')=='official_naver_image_api'}-{None}
    return next(iter(urls)) if len(urls)==1 else None


def parse_results(raw):
    try:data=json.loads(raw)
    except (ValueError,UnicodeError):raise RemoteError('네이버 이미지 검색 JSON을 읽지 못했습니다.') from None
    if not isinstance(data,dict) or not isinstance(data.get('items'),list) or len(data['items'])>50:
        raise RemoteError('네이버 이미지 검색 응답 형식이 바뀌었습니다.')
    candidates=[];seen=set()
    for item in data['items']:
        if not isinstance(item,dict):continue
        if not all(isinstance(item.get(key),str) and item[key].startswith('https://') for key in ('thumbnail','link')):continue
        preview=clean_url(item.get('thumbnail',''),'https://search.pstatic.net/')
        original=clean_url(item.get('link',''),'https://search.naver.com/')
        if not preview or not original:continue
        try:validate_url(preview,['search.pstatic.net'])
        except RemoteError:continue
        if original in seen:continue
        seen.add(original)
        title=unescape(re.sub('<[^>]+>','',str(item.get('title',''))))[:500]
        candidates.append(ImageCandidate(preview,'naver:'+hashlib.sha256(original.encode()).hexdigest(),title,'thumbnail',
            provenance=[dict(method='official_naver_image_api',linked_image_url=original,
                             person_confirmed=False,original_verified=False)]))
    return data,candidates


class NaverImageProvider:
    name=SPEC['id']
    def __init__(self,online):self.online=online;online.web.repo.save_spec(SPEC)
    def search_images(self,query,cursor,cancelled):
        if cancelled.is_set():raise Cancelled()
        if cursor is not None and (not isinstance(cursor,dict) or cursor.get('query')!=query):
            raise RemoteError('네이버 검색어와 추가 페이지가 일치하지 않습니다.',blocked=True)
        start=1 if cursor is None else cursor.get('start')
        if type(start) is not int or not 1<=start<=1000:raise RemoteError('네이버 이미지 검색 페이지 범위를 확인하세요.')
        repo=self.online.web.repo;repo.require(self.name,'metadata_search')
        try:client_id,secret=naver_credentials.load(self.online.settings.root)
        except (RuntimeError,ValueError) as error:raise RemoteError(str(error),blocked=True) from None
        # This documented API explicitly authorizes programmatic authenticated
        # calls. Its crawler robots policy is not a contract for API clients.
        # Credentials are confined to the single API host, including redirects.
        client=HttpClient(allowed_hosts=['openapi.naver.com'],public_only=True,headers={
            'X-Naver-Client-Id':client_id,'X-Naver-Client-Secret':secret})
        url=ENDPOINT+'?'+urlencode(dict(query=query,display=50,start=start,sort='sim',filter='all'))
        try:
            with client.open(url,cancelled) as response:raw=response.read(4*1024*1024+1)
        except RemoteError as error:
            if error.status in (401,403):raise RemoteError('네이버 API 인증 또는 사용 권한을 확인하세요. 발급 앱의 검색 API 설정도 확인하세요.',blocked=True,status=error.status) from None
            raise
        if len(raw)>4*1024*1024:raise RemoteError('네이버 검색 응답 크기 한도를 초과했습니다.')
        data,candidates=parse_results(raw)
        total=data.get('total');display=data.get('display');returned_start=data.get('start')
        if type(total) is not int or total<0 or type(display) is not int or type(returned_start) is not int or returned_start!=start or not 0<=display<=50:
            raise RemoteError('네이버 검색 페이지 메타데이터를 확인하지 못했습니다.')
        if cancelled.is_set():raise Cancelled()
        repo.require(self.name,'metadata_search')
        page=repo.add_page(self.name,'https://search.naver.com/search.naver?'+urlencode(dict(where='image',query=query,start=start)))
        repo.index_page(repo.page(page),ParsedPage(query,candidates,metadata=dict(search_managed=True,query=query,person_confirmed=False)))
        rows=repo.images_for_urls(page,[item.url for item in candidates]);allow_reviewed_previews(repo,self.name,rows)
        following=start+display
        cursor=dict(query=query,start=following) if display and following<=min(total,1000) else None
        return ['web:'+row['id'] for row in rows],cursor
