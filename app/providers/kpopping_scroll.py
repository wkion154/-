"""Bounded incremental discovery on an already-open, policy-checked browser page.
Does not guess endpoints, clear artist filters, bypass barriers or download originals.
"""
import time
from urllib.parse import urlsplit,parse_qs
from .kpopping import parse_observation
from .http import Cancelled,RemoteError

SCRIPT='''(images)=>images.slice(0,600).map(i=>({src:i.src,currentSrc:i.currentSrc,width:i.naturalWidth,height:i.naturalHeight,alt:i.alt}))'''


def artist_filter(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname!='kpopping.com' or p.path!='/kpics' or p.username or p.password:raise RemoteError('Kpopping 사진 목록 페이지가 아닙니다.',blocked=True)
    query=parse_qs(p.query)
    return tuple(query.get('idol',[])),tuple(query.get('idolName',[]))


def capture_batch(page,query,cursor,cancelled,*,rounds=4,wait_ms=700):
    if not 1<=rounds<=12:raise ValueError('한 배치의 스크롤 횟수는 1~12입니다.')
    current_filter=artist_filter(page.url)
    if not current_filter[0]:raise RemoteError('인물 필터가 없는 전체 사이트 목록은 자동 수집하지 않습니다.',blocked=True)
    cursor=cursor or {};seen=set(cursor.get('seen',[]))
    if cursor and cursor.get('artist_filter')!=[list(v) for v in current_filter]:raise RemoteError('스크롤 중 검색 인물 필터가 바뀌었습니다.',blocked=True)
    all_rows=[];new=[];empty_rounds=0
    for iteration in range(rounds):
        if cancelled.is_set():raise Cancelled()
        if artist_filter(page.url)!=current_filter:raise RemoteError('검색 인물 필터가 풀려 수집을 중단했습니다.',blocked=True)
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
        if empty_rounds>=3:break
        if iteration+1<rounds:
            page.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
            page.wait_for_timeout(wait_ms)
    # Empty batches do not prove exhaustion: lazy loading can be slow. Caller can retry.
    next_cursor=dict(artist_filter=[list(v) for v in current_filter],seen=list(seen),batch=int(cursor.get('batch',0))+1)
    return new,next_cursor,dict(new_photo_candidates=len(new),consecutive_empty_rounds=empty_rounds,exhaustion_verified=False,stage1_complete=False)
