"""Reviewed HTML search template adapter using the existing web image pipeline."""
from urllib.parse import quote,urlsplit
from .http import validate_url,RemoteError


class PageSearchProvider:
    def __init__(self,web,source,template):
        self.web,self.name,self.template=web,source,template
        if source=='wikimedia':raise ValueError('Wikimedia는 통합 검색에서 제외됐습니다.')
        if template.count('{query}')!=1:raise ValueError('검색 URL에 {query}가 한 번 필요합니다.')
        spec=web.repo.spec(source)
        if spec.adapter not in ('html','rendered'):raise ValueError('검토된 공개 페이지 출처가 필요합니다.')
        validate_url(template.replace('{query}','test'),spec.domains)
    def search_images(self,query,cursor,cancelled):
        if cursor is not None:raise ValueError('이 어댑터의 다음 페이지 탐색은 아직 검증하지 않았습니다.')
        self.web.repo.require(self.name,'metadata_search')
        spec=self.web.repo.spec(self.name)
        url=self.template.replace('{query}',quote(query,safe=''))
        mode='rendered' if spec.adapter=='rendered' else 'html'
        page=self.web.repo.add_page(self.name,url,mode=mode)
        self.web.scan(page,cancelled)
        return ['web:'+r['id'] for r in self.web.repo.images(page_id=page)],None
