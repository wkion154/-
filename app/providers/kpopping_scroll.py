"""Bounded incremental discovery on an already-open, policy-checked browser page.
Does not guess endpoints, clear artist filters, bypass barriers or download originals.
"""
import time
import re
from urllib.parse import urlsplit,parse_qs
from .kpopping import parse_observation
from .http import Cancelled,RemoteError

SCRIPT='''(images)=>images.slice(0,600).map(i=>({src:i.src,currentSrc:i.currentSrc,width:i.naturalWidth,height:i.naturalHeight,alt:i.alt}))'''


def artist_filter(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname!='kpopping.com' or p.port not in (None,443) or p.path!='/kpics' or p.username or p.password:raise RemoteError('Kpopping 사진 목록 페이지가 아닙니다.',blocked=True)
    query=parse_qs(p.query)
    groups=tuple(query.get('group',[]))
    if groups:
        if query.get('idol') or query.get('idolName'):
            raise RemoteError('인물과 그룹 필터가 함께 지정돼 검색 대상을 확인할 수 없습니다.',blocked=True)
        # Keep existing person cursors compatible; distinguish group IDs so an
        # equal-looking ID can never be replayed against a different entity type.
        return tuple('group:'+value for value in groups),tuple(query.get('groupName',[]))
    return tuple(query.get('idol',[])),tuple(query.get('idolName',[]))


def selected_artist_pattern(url):
    ids,names=artist_filter(url)
    if len(ids)!=1 or len(names)!=1 or not names[0].strip():
        raise RemoteError('검색 인물·그룹 ID와 이름을 하나씩 확인해야 합니다.',blocked=True)
    # Observed in the current public Kpics UI: the selected entity is shown
    # as a visible "Filtering: <name>" button after client hydration.
    name=r'\s+'.join(re.escape(part) for part in names[0].split())
    return re.compile(r'^\s*Filtering:\s*'+name+r'\s*$',re.I)


def require_visible_artist(page):
    badge=page.get_by_role('button',name=selected_artist_pattern(page.url))
    if not badge.count() or not any(badge.nth(i).is_visible() for i in range(badge.count())):
        raise RemoteError('주소의 인물 필터가 실제 화면에 적용됐는지 확인하지 못했습니다. 전체 목록을 검색 결과로 수집하지 않습니다.',blocked=True)


def capture_batch(page,query,cursor,cancelled,*,rounds=4,wait_ms=700,stop_on_stale=True):
    if not 1<=rounds<=12:raise ValueError('한 배치의 스크롤 횟수는 1~12입니다.')
    current_filter=artist_filter(page.url)
    if not current_filter[0]:raise RemoteError('인물 필터가 없는 전체 사이트 목록은 자동 수집하지 않습니다.',blocked=True)
    cursor=cursor or {};seen=set(cursor.get('seen',[]))
    if cursor and cursor.get('artist_filter')!=[list(v) for v in current_filter]:raise RemoteError('스크롤 중 검색 인물 필터가 바뀌었습니다.',blocked=True)
    all_rows=[];new=[];empty_rounds=0
    for iteration in range(rounds):
        if cancelled.is_set():raise Cancelled()
        if artist_filter(page.url)!=current_filter:raise RemoteError('검색 인물 필터가 풀려 수집을 중단했습니다.',blocked=True)
        require_visible_artist(page)
        title=page.title().lower()
        if any(word in title for word in ('captcha','just a moment','access denied','verify you are human')):raise RemoteError('접근 확인 화면입니다. 우회하지 않습니다.',blocked=True)
        rows=page.locator('img').evaluate_all(SCRIPT)
        observed=[]
        for row in rows:
            url=row.get('currentSrc') or row.get('src')
            if url:observed.append(dict(url=url,alt=row.get('alt',''),dimension_evidence='loaded_currentSrc_only'))
        parsed=parse_observation(dict(page_url=page.url,page_title=page.title(),query=query,state='diagnostic_completed',images=observed))
        added=0
        for item in parsed.candidates:
            if item.logical_key in seen:continue
            seen.add(item.logical_key);new.append(item);added+=1
        empty_rounds=empty_rounds+1 if not added else 0
        if stop_on_stale and empty_rounds>=3:break
        if iteration+1<rounds:
            page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
            page.wait_for_timeout(wait_ms)
    # Empty batches do not prove exhaustion: lazy loading can be slow. Caller can retry.
    next_cursor=dict(artist_filter=[list(v) for v in current_filter],seen=list(seen),batch=int(cursor.get('batch',0))+1)
    return new,next_cursor,dict(new_photo_candidates=len(new),consecutive_empty_rounds=empty_rounds,exhaustion_verified=False,stage1_complete=False)


def capture_replayed_batch(page,query,batch,previous,cancelled):
    """Retain discoveries at every replay position on a virtualized page.

    Reopening is still less efficient than a persistent browser, but skipping
    straight to the requested position loses photos that disappeared from DOM.
    Previously seen logical keys remain in the cursor across worker processes.
    """
    if type(batch) is not int or not 0<=batch<=20:raise ValueError('추가 검색 배치 한도입니다.')
    cursor=previous;items=[]
    for _ in range(batch+1):
        rows,cursor,_=capture_batch(page,query,cursor,cancelled,rounds=4,stop_on_stale=False)
        items.extend(rows)
    return items,cursor
