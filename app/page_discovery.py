"""Bounded, non-executing HTML image discovery. URLs are never up-sized by guessing."""
from dataclasses import dataclass,field
from html.parser import HTMLParser
import hashlib
import json
from datetime import datetime
import ipaddress
import re
from urllib.parse import urljoin,urlsplit,urlunsplit
from .providers.http import RemoteError


def clean_url(value,base):
    try:
        value=str(value).strip()
        if not value:return None
        if len(value)>8192 or any(ord(c)<32 for c in value):return None
        p=urlsplit(urljoin(base,value))
        host=(p.hostname or '').lower()
        if p.scheme!='https' or not host or p.username or p.password or p.port not in (None,443):return None
        if host=='localhost' or host.endswith(('.localhost','.local','.internal')):return None
        try:
            if not ipaddress.ip_address(host).is_global:return None
        except ValueError:pass
        return urlunsplit(('https',p.netloc.lower(),p.path or '/',p.query,''))
    except ValueError:return None


def srcset_values(value):
    if len(value)>65536:return []
    result=[];position=0
    while position<len(value) and len(result)<100:
        while position<len(value) and (value[position].isspace() or value[position]==','):position+=1
        start=position
        while position<len(value) and not value[position].isspace():position+=1
        token=value[start:position]
        descriptor=''
        if token.endswith(','):
            token=token.rstrip(',')
        else:
            start=position
            while position<len(value) and value[position]!=',':position+=1
            descriptor=value[start:position].strip()
        if position<len(value) and value[position]==',':position+=1
        if not token or token.startswith(('data:','blob:','javascript:')):continue
        if descriptor and not re.fullmatch(r'\d+(?:\.\d+)?[wx]',descriptor):continue
        result.append((token,descriptor))
    return result

@dataclass
class ImageCandidate:
    url:str
    logical_key:str
    title:str=''
    role:str='page_image'
    declared_width:int|None=None
    declared_height:int|None=None
    provenance:list=field(default_factory=list)

@dataclass
class ParsedPage:
    title:str
    candidates:list[ImageCandidate]
    warnings:list[str]=field(default_factory=list)
    metadata:dict=field(default_factory=dict)
    published:str|None=None

class ImageParser(HTMLParser):
    def __init__(self,url,original_attributes=(),limit=300):
        super().__init__(convert_charrefs=True)
        self.url,self.base,self.original_attributes,self.limit=url,url,original_attributes,limit
        self.items={};self.title_parts=[];self.in_title=False;self.picture=None;self.counter=0;self.barriers=[];self.metadata={};self.in_ld=False;self.ld_parts=[]
    def add(self,url,logical,title,role,attrs,descriptor='',via='img'):
        normalized=clean_url(url,self.base)
        if not normalized or len(self.items)>=self.limit:return
        def dimension(value):
            try:
                number=int(value)
                return number if 0<number<=100000 else None
            except (TypeError,ValueError):return None
        width=dimension(attrs.get('width'));height=dimension(attrs.get('height'))
        if descriptor.endswith('w'):width=dimension(descriptor[:-1])
        evidence=dict(element=via,descriptor=descriptor,relationship='source_declared_variant_not_verified_identity')
        if normalized in self.items:self.items[normalized].provenance.append(evidence);return
        self.items[normalized]=ImageCandidate(normalized,logical,title[:512],role,width,height,[evidence])
    def handle_starttag(self,tag,attributes):
        attrs={k:(v or '') for k,v in attributes}
        if tag=='title':self.in_title=True
        if tag=='script' and attrs.get('type','').lower()=='application/ld+json':self.in_ld=True;self.ld_parts=[]
        if tag=='meta' and (attrs.get('property') or attrs.get('name','')).lower() in ('article:published_time','og:published_time','datepublished','publishdate'):
            self.metadata['published_raw']=attrs.get('content','')[:200]
        if tag=='base' and attrs.get('href'):
            proposed=clean_url(attrs['href'],self.url)
            if proposed and urlsplit(proposed).hostname==urlsplit(self.url).hostname:self.base=proposed
        if tag=='input' and attrs.get('type','').lower()=='password':self.barriers.append('login')
        if tag in ('iframe','script') and any(x in attrs.get('src','').lower() for x in ('recaptcha','hcaptcha','challenges.cloudflare.com')):self.barriers.append('captcha')
        if 'captcha' in (attrs.get('id','')+' '+attrs.get('class','')).lower():self.barriers.append('captcha')
        if tag=='picture':
            self.counter+=1;self.picture='picture:'+str(self.counter)
        if tag in ('img','source'):
            if attrs.get('data-kidol-visible')=='false':return
            if tag=='source' and not self.picture:return
            self.counter+=1;logical=self.picture or 'img:'+str(self.counter)
            title=attrs.get('alt','') or attrs.get('title','')
            for name in ('src','data-src'):
                if attrs.get(name):self.add(attrs[name],logical,title,'page_image',attrs,via=tag+':'+name)
            for name in ('srcset','data-srcset'):
                for url,descriptor in srcset_values(attrs.get(name,'')):self.add(url,logical,title,'responsive',attrs,descriptor,tag+':'+name)
            for name in self.original_attributes:
                if attrs.get(name):self.add(attrs[name],logical,title,'source_declared_original',attrs,via=tag+':'+name)
        if tag=='meta' and (attrs.get('property','').lower() in ('og:image','og:image:url','og:image:secure_url') or attrs.get('name','').lower()=='twitter:image'):
            self.add(attrs.get('content',''),'metadata','', 'page_metadata',{},via=attrs.get('property',attrs.get('name','meta')))
        if tag=='link' and attrs.get('rel','').lower()=='image_src':self.add(attrs.get('href',''),'metadata','','page_metadata',{},via='link:image_src')
    def handle_endtag(self,tag):
        if tag=='title':self.in_title=False
        if tag=='picture':self.picture=None
        if tag=='script' and self.in_ld:
            self.in_ld=False
            try:self.structured(json.loads(''.join(self.ld_parts)))
            except (ValueError,TypeError,RecursionError):pass
            self.ld_parts=[]
    def structured(self,data):
        nodes=data if isinstance(data,list) else data.get('@graph',[data]) if isinstance(data,dict) else []
        for node in nodes[:50]:
            if not isinstance(node,dict):continue
            if isinstance(node.get('datePublished'),str):self.metadata.setdefault('published_raw',node['datePublished'][:200])
            images=[node] if node.get('@type')=='ImageObject' else node.get('image',[])
            if not isinstance(images,list):images=[images]
            for image in images[:50]:
                if isinstance(image,str):self.add(image,'structured','', 'page_metadata',{},via='jsonld:image')
                elif isinstance(image,dict):
                    self.add(image.get('contentUrl',image.get('url','')),'structured',str(image.get('caption',image.get('name',''))),'page_metadata',{'width':image.get('width'),'height':image.get('height')},via='jsonld:ImageObject')
                    if image.get('thumbnailUrl'):self.add(image['thumbnailUrl'],'structured','','thumbnail',{},via='jsonld:thumbnail')

    def handle_data(self,data):
        if self.in_ld and sum(map(len,self.ld_parts))<65536:self.ld_parts.append(data[:65536])
        if self.in_title and sum(map(len,self.title_parts))<1024:self.title_parts.append(data[:1024])


def discover_html(html,url,original_attributes=(),limit=300):
    if len(html)>2*1024*1024:raise RemoteError('페이지 크기 한도(2MiB)를 초과했습니다.')
    parser=ImageParser(url,original_attributes,limit);parser.feed(html)
    if parser.barriers:raise RemoteError('로그인/접근 확인 페이지입니다. 우회하지 않습니다.',blocked=True)
    published=None
    raw=parser.metadata.get('published_raw')
    if raw:
        try:published=datetime.fromisoformat(raw.replace('Z','+00:00')).isoformat()
        except ValueError:pass
    return ParsedPage(' '.join(parser.title_parts).strip()[:512],list(parser.items.values()),metadata=parser.metadata,published=published)


def choose_version(candidates,download_allowed):
    verified=[x for x in candidates if download_allowed(x) and x.get('width') and x.get('height') and x.get('sha256') and x.get('role')!='capture']
    if not verified:return None
    # Generic HTML 'data-original' is only a source claim, not proof of an original.
    return max(verified,key=lambda x:(x.get('role')=='verified_original',x['width']*x['height'],x.get('mime') in ('PNG','TIFF','image/png','image/tiff'),bool(x.get('sha256'))))
