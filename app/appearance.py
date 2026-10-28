"""Explainable region-color suggestions; never asserts clothing/event identity."""
from .analysis_filters import analysis_permission_sql
from .color_analysis import delta_e


def _bins(conn,identity,region):
    return [dict(r) for r in conn.execute('SELECT l,a,b,weight,total FROM analysis_colors WHERE entry_id=? AND region=? ORDER BY weight DESC LIMIT 3',(identity,region))]


def nearby(conn,identity):
    seed=conn.execute('SELECT x.engine,x.sha256 FROM image_analysis x JOIN gallery_items e ON e.id=x.entry_id AND e.sha256=x.sha256 WHERE x.entry_id=? AND x.stale=0 AND '+analysis_permission_sql(),(identity,)).fetchone()
    if not seed:return {}
    found={}
    for region in ('background','torso'):
        seed_bins=_bins(conn,identity,region)
        if not seed_bins:continue
        lead=seed_bins[0];l,a,b=lead['l'],lead['a'],lead['b'];radius=12.
        rows=conn.execute("SELECT c.entry_id,x.engine,x.sha256 FROM color_space s JOIN analysis_colors c ON c.id=s.id JOIN image_analysis x ON x.entry_id=c.entry_id JOIN gallery_items e ON e.id=x.entry_id AND e.sha256=x.sha256 WHERE s.l0<=? AND s.l1>=? AND s.a0<=? AND s.a1>=? AND s.b0<=? AND s.b1>=? AND c.region=? AND x.stale=0 AND e.kind='asset' AND e.id<>? AND "+analysis_permission_sql()+" GROUP BY c.entry_id ORDER BY SUM(c.weight)*1.0/MAX(c.total) DESC LIMIT 128",(l+radius,l-radius,a+radius,a-radius,b+radius,b-radius,region,identity)).fetchall()
        for row in rows:
            other=_bins(conn,row['entry_id'],region)
            pairs=[delta_e((p['l'],p['a'],p['b']),(q['l'],q['a'],q['b'])) for p in seed_bins for q in other if p['weight']/p['total']>=.1 and q['weight']/q['total']>=.1]
            if not pairs or min(pairs)>radius:continue
            found.setdefault(row['entry_id'],[]).append(dict(region=region,delta_e=round(min(pairs),3),seed_engine=seed['engine'],candidate_engine=row['engine'],seed_sha=seed['sha256'],candidate_sha=row['sha256']))
    return found
