"""Parse user-observed Kpopping browser results; no unverified live endpoint guesses."""
import hashlib
from urllib.parse import urlsplit,parse_qs
from ..page_discovery import ParsedPage,ImageCandidate


def _public_source(value,host,path=None):
    try:
        p=urlsplit(value)
        return p.scheme=='https' and p.hostname==host and not p.username and not p.password and p.port in (None,443) and (path is None or p.path==path)
    except (TypeError,ValueError):return False


def provided_file_url(preview_url):
    """Read the exact CDN address supplied in an observed image wrapper.

    This is a provided file candidate, not proof of maximum/original quality.
    No size parameter, filename or extension is changed or synthesized.
    """
    if not isinstance(preview_url,str) or len(preview_url)>8192:return None
    if not _public_source(preview_url,'kpopping.com','/api/kpics-image'):return None
    values=parse_qs(urlsplit(preview_url).query).get('url',[])
    if len(values)!=1:return None
    candidate=values[0]
    if not _public_source(candidate,'cdn.kpopping.com') or not urlsplit(candidate).path.startswith('/kpics/'):return None
    if any(ord(ch)<32 for ch in candidate):return None
    return candidate


def parse_observation(report):
    page_url=report.get('page_url','')
    if not _public_source(page_url,'kpopping.com','/kpics'):raise ValueError('관측된 Kpopping kpics 검색 페이지가 필요합니다.')
    if report.get('state')!='diagnostic_completed':raise ValueError('완료된 브라우저 관측 결과가 필요합니다.')
    groups={};ignored=0;rows=report.get('images',[])
    if not isinstance(rows,list) or len(rows)>1000:raise ValueError('진단 이미지 목록 한도를 확인하세요.')
    for row in rows:
        if not isinstance(row,dict):ignored+=1;continue
        url=row.get('url','')
        if not _public_source(url,'kpopping.com','/api/kpics-image'):ignored+=1;continue
        params=parse_qs(urlsplit(url).query);declared=params.get('url',[])
        if len(declared)!=1 or not _public_source(declared[0],'cdn.kpopping.com') or not urlsplit(declared[0]).path.startswith('/kpics/'):
            ignored+=1;continue
        group=groups.setdefault(declared[0],dict(title=str(row.get('alt',''))[:512],variants={}))
        group['variants'][url]=dict(url=url,size=params.get('size',['unknown'])[0],dimension_evidence=row.get('dimension_evidence','legacy_unverified_preview_dimensions'))
    candidates=[]
    for declared,group in groups.items():
        variants=list(group['variants'].values())
        # Choose an actually observed display URL, never construct an "original" URL.
        display=max(variants,key=lambda item:{'thumb':1,'medium':2}.get(item['size'],0))
        evidence=dict(method='user_supplied_browser_observation',page_url=page_url,declared_resource_url=declared,observed_variants=variants,original_verified=False,dimensions_verified=False,person_confirmed=False,rights_granted=False)
        candidates.append(ImageCandidate(display['url'],'kpopping:'+hashlib.sha256(declared.encode()).hexdigest(),group['title'],'observed_preview',None,None,[evidence]))
    return ParsedPage(str(report.get('page_title','Kpopping observation')),candidates,['원본·인물 일치·이용 권한 미확인'],dict(query=str(report.get('query',''))[:200],page_url=page_url,raw_url_count=len(rows),ignored_non_photo_urls=ignored,unique_photo_candidates=len(candidates),input_limit_reached=len(rows)>=100,automated_search_verified=report.get('search_submitted') is True,stage1_complete=False))
