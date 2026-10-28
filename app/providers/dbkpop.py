"""DBKpop's public WordPress post search and explicitly supplied gallery files."""
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlencode,urlsplit
from .http import RemoteError,Cancelled,validate_url
from .search_previews import allow_reviewed_previews
from ..page_discovery import ImageCandidate,ParsedPage,clean_url

SPEC=dict(id='dbkpop-search',name='DBKpop · 공개 게시물/사진 검색',category='fan',
          adapter='html',domains=['dbkpop.com'],policy_urls=['https://dbkpop.com/robots.txt'])
ENDPOINT='https://dbkpop.com/wp-json/wp/v2/posts'


def upload_url(value):
    if not isinstance(value,str):return None
    url=clean_url(value,'https://dbkpop.com/')
    if url and urlsplit(url).hostname=='dbkpop.com' and urlsplit(url).path.startswith('/wp-content/uploads/'):
        return url
    return None


class Gallery(HTMLParser):
    def __init__(self):super().__init__();self.anchors=[];self.candidates=[]
    def handle_starttag(self,tag,attributes):
        attrs=dict(attributes)
        if tag=='a':self.anchors.append(upload_url(attrs.get('href')))
        if tag!='img' or len(self.candidates)>=300:return
        preview=upload_url(attrs.get('src'))
        try:width=int(attrs.get('width',0));height=int(attrs.get('height',0))
        except (TypeError,ValueError):return
        # Only explicitly small image variants qualify as display thumbnails.
        if not preview or not (0<width<=1024 and 0<height<=1024):return
        supplied=self.anchors[-1] if self.anchors else None
        evidence=dict(method='wordpress_post_gallery',provided_resource_url=supplied,
                      original_verified=False,person_confirmed=False)
        key=hashlib.sha256((supplied or preview).encode()).hexdigest()
        self.candidates.append(ImageCandidate(preview,'dbkpop:'+key,
            (attrs.get('alt') or attrs.get('title') or '')[:500],'thumbnail',width,height,[evidence]))
    def handle_endtag(self,tag):
        if tag=='a' and self.anchors:self.anchors.pop()


def parse_posts(raw):
    try:posts=json.loads(raw)
    except (ValueError,UnicodeError):raise RemoteError('DBKpop 검색 JSON을 읽지 못했습니다.') from None
    if not isinstance(posts,list) or len(posts)>5:raise RemoteError('DBKpop 검색 응답 형식 또는 배치 크기가 바뀌었습니다.')
    parsed=[]
    for post in posts:
        if not isinstance(post,dict):continue
        page=clean_url(post.get('link',''),'https://dbkpop.com/')
        if not page:continue
        try:validate_url(page,SPEC['domains'])
        except RemoteError:continue
        title=post.get('title',{});content=post.get('content',{})
        if not isinstance(title,dict) or not isinstance(content,dict):continue
        rendered=content.get('rendered','')
        if not isinstance(rendered,str) or len(rendered)>2*1024*1024:continue
        gallery=Gallery();gallery.feed(rendered)
        seen=set();candidates=[]
        for candidate in gallery.candidates:
            if candidate.logical_key not in seen:
                seen.add(candidate.logical_key);candidates.append(candidate)
        label=unescape(re.sub('<[^>]+>','',str(title.get('rendered',''))))[:500]
        parsed.append((page,ParsedPage(label,candidates,metadata=dict(search_managed=True,
                        person_confirmed=False,original_verified=False))))
    return parsed


class DBKpopProvider:
    name=SPEC['id']
    identity_search=True
    def __init__(self,online):self.online=online;online.web.repo.save_spec(SPEC)
    def search_images(self,query,cursor,cancelled):
        if cancelled.is_set():raise Cancelled()
        if cursor is not None and (not isinstance(cursor,dict) or cursor.get('query')!=query):
            raise RemoteError('DBKpop 검색어와 추가 페이지가 일치하지 않습니다.',blocked=True)
        page=1 if cursor is None else cursor.get('page')
        if type(page) is not int or not 1<=page<=1000:raise RemoteError('DBKpop 추가 페이지 범위를 확인하세요.')
        web=self.online.web;repo=web.repo;repo.require(self.name,'metadata_search')
        url=ENDPOINT+'?'+urlencode(dict(search=query,per_page=5,page=page,_embed='wp:featuredmedia'))
        raw,headers=web.read(self.name,url,cancelled,8*1024*1024,'application/json')
        entries=[]
        for page_url,parsed in parse_posts(raw):
            if cancelled.is_set():raise Cancelled()
            repo.require(self.name,'metadata_search')
            page_id=repo.add_page(self.name,page_url)
            parsed.metadata['query']=query
            repo.index_page(repo.page(page_id),parsed)
            rows=repo.images_for_urls(page_id,[item.url for item in parsed.candidates])
            allow_reviewed_previews(repo,self.name,rows)
            entries.extend('web:'+row['id'] for row in rows)
        total=next((value for key,value in headers.items() if key.lower()=='x-wp-totalpages'),None)
        try:has_more=page<min(int(total),1000)
        except (TypeError,ValueError):has_more=False
        return list(dict.fromkeys(entries)),dict(page=page+1,query=query) if has_more else None
