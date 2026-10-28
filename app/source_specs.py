"""Declarative domain adapters; no external Python/JavaScript plugin imports."""
from dataclasses import dataclass,asdict
import hashlib
import json
import re

OPERATIONS=('metadata_search','thumbnail','original_download','local_analysis','ai_restore_input','result_storage','browser_render')
OPERATION_LABELS={'metadata_search':'메타데이터 탐색','thumbnail':'썸네일 표시','original_download':'원본·실측용 임시 다운로드','local_analysis':'로컬 이미지 분석','ai_restore_input':'AI 복원 입력','result_storage':'AI 결과 저장·이용','browser_render':'브라우저 DOM 렌더링'}
STATES=('allowed','restricted','review','denied')
CATEGORIES=('official','social','press','fan','user')
CAPABILITIES={
 'candidate':frozenset(),
 'local':frozenset({'local_import'}),
 'wikimedia':frozenset({'discover','metadata','photos','thumbnail','download'}),
 'html':frozenset({'page_discovery','declared_versions','metadata','download'}),
 'direct':frozenset({'direct_url','metadata','download'}),
 'rendered':frozenset({'page_discovery','rendered_dom','declared_versions','metadata','download'}),
}

@dataclass(frozen=True)
class SourceSpec:
    id:str
    name:str
    category:str
    adapter:str
    domains:tuple[str,...]=()
    policy_urls:tuple[str,...]=()
    original_attributes:tuple[str,...]=()
    interval_seconds:float=1.5
    render_allow_xhr:bool=False

    @classmethod
    def load(cls,data):
        if not isinstance(data,dict) or set(data)-set(cls.__dataclass_fields__):raise ValueError('알 수 없는 출처 설정 필드입니다. 실행 코드 플러그인은 지원하지 않습니다.')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',str(data.get('id',''))):raise ValueError('출처 ID를 확인하세요.')
        if data.get('category') not in CATEGORIES or data.get('adapter') not in CAPABILITIES:raise ValueError('출처 유형/어댑터를 확인하세요.')
        if not isinstance(data.get('name'),str) or not 1<=len(data['name'])<=120:raise ValueError('출처 이름을 확인하세요.')
        options=dict(data)
        for key in ('domains','policy_urls','original_attributes'):
            values=data.get(key,())
            if not isinstance(values,(list,tuple)) or len(values)>30 or not all(isinstance(x,str) for x in values):raise ValueError('출처 설정 목록이 잘못되었습니다.')
            options[key]=tuple(values)
        for host in options['domains']:
            if len(host)>253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host) or '.' not in host or '..' in host:raise ValueError('와일드카드 없는 정확한 도메인을 입력하세요.')
        for attr in options['original_attributes']:
            if not re.fullmatch(r'data-[a-z0-9-]{1,50}',attr):raise ValueError('허용된 data-* 속성만 설정할 수 있습니다.')
        from urllib.parse import urlsplit
        for url in options['policy_urls']:
            p=urlsplit(url)
            if p.scheme!='https' or not p.hostname or p.username or p.password:raise ValueError('정책 문서는 HTTPS URL이어야 합니다.')
        interval=data.get('interval_seconds',1.5)
        if not isinstance(interval,(int,float)) or not 1<=interval<=3600:raise ValueError('요청 간격은 최소 1초입니다.')
        if not isinstance(data.get('render_allow_xhr',False),bool):raise ValueError('렌더링 설정을 확인하세요.')
        return cls(**options)

    @property
    def capabilities(self):return CAPABILITIES[self.adapter]
    def dictionary(self):return asdict(self)
    @property
    def digest(self):return hashlib.sha256(json.dumps(asdict(self),sort_keys=True,ensure_ascii=False).encode()).hexdigest()

# These entries are candidates, never assertions of current API/site support.
CATALOG=(
 SourceSpec('local','사용자 로컬 파일','user','local'),
 SourceSpec('wikimedia','Wikidata / Wikimedia Commons','fan','wikimedia',('commons.wikimedia.org','upload.wikimedia.org','www.wikidata.org','query.wikidata.org','www.mediawiki.org','foundation.wikimedia.org','doc.wikimedia.org')),
 SourceSpec('official-sites','공식 아티스트·소속사 사이트','official','candidate'),
 SourceSpec('x','X 공식 API 검토 후보','social','candidate',('x.com','api.x.com')),
 SourceSpec('instagram','Instagram 공식 API 검토 후보','social','candidate',('www.instagram.com','graph.instagram.com')),
 SourceSpec('naver','네이버 연예·뉴스 검토 후보','press','candidate'),
 SourceSpec('dispatch','Dispatch 검토 후보','press','candidate'),
 SourceSpec('osen','OSEN 검토 후보','press','candidate'),
 SourceSpec('newsen','Newsen 검토 후보','press','candidate'),
 SourceSpec('starnews','스타뉴스 검토 후보','press','candidate'),
 SourceSpec('flickr','Flickr API 검토 후보','fan','candidate',('api.flickr.com',)),
 SourceSpec('kpopping','Kpopping 검토 후보 · AI 이용 별도 확인','fan','candidate',('kpopping.com',)),
 SourceSpec('fan-sites','팬 촬영 사이트 검토 후보','fan','candidate'),
)
