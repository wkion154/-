"""Exact Hamming-radius candidate generation over a bounded-memory band index."""
import re
import time
from .providers.http import Cancelled

ALGORITHM='dhash64-thumb-v1'
SHIFTS=(0,13,26,39,52)
WIDTHS=(13,13,13,13,12)


def bands(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9a-fA-F]{16}',value):raise ValueError('유사 특징 형식을 확인하세요.')
    bits=int(value,16)
    return tuple((bits>>shift)&((1<<width)-1) for shift,width in zip(SHIFTS,WIDTHS))


def store_feature(conn,identity,sha256,dhash,updated=None):
    values=bands(dhash)
    conn.execute('INSERT INTO perceptual_features VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET sha256=excluded.sha256,algorithm=excluded.algorithm,b0=excluded.b0,b1=excluded.b1,b2=excluded.b2,b3=excluded.b3,b4=excluded.b4,updated=excluded.updated',
                 (identity,sha256,ALGORITHM,*values,time.time() if updated is None else updated))


def backfill(conn):
    cursor=conn.execute('SELECT id,sha256,dhash FROM web_images WHERE dhash IS NOT NULL AND sha256 IS NOT NULL')
    while rows:=cursor.fetchmany(256):
        for row in rows:
            try:store_feature(conn,row['id'],row['sha256'],row['dhash'])
            except ValueError:continue


def candidates(conn,identity,sha256,dhash,cancelled,after='',limit=512):
    """Five disjoint bands: <=4 differing bits implies at least one equal band.

    Paging by item ID keeps allocations bounded even for uniform/low-entropy
    hashes. No top-10k cutoff. Exact distance and revision checks follow SQL.
    """
    values=bands(dhash)
    if not 1<=limit<=512:raise ValueError('후보 페이지 크기를 확인하세요.')
    if cancelled.is_set():raise Cancelled()
    union=' UNION '.join(f'SELECT item_id FROM (SELECT f.item_id FROM perceptual_features f JOIN web_images w ON w.id=f.item_id AND w.sha256=f.sha256 WHERE f.algorithm=? AND f.b{i}=? AND f.item_id>? AND f.item_id<>? AND w.dhash IS NOT NULL ORDER BY f.item_id LIMIT ?)' for i in range(5))
    args=[]
    for value in values:args.extend((ALGORITHM,value,after,identity,limit+1))
    # Every branch contributes only a bounded sorted prefix before UNION, avoiding
    # a huge uniform-hash temporary set on every page.
    conn.set_progress_handler(lambda:int(cancelled.is_set()),1000)
    try:
        rows=conn.execute('SELECT w.id,w.sha256,w.dhash FROM ('+union+') c JOIN web_images w ON w.id=c.item_id ORDER BY w.id LIMIT ?',(*args,limit+1)).fetchall()
    except Exception:
        if cancelled.is_set():raise Cancelled() from None
        raise
    finally:conn.set_progress_handler(None,0)
    more=len(rows)>limit
    return rows[:limit],more
