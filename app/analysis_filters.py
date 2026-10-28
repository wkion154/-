from dataclasses import dataclass,asdict
import math
from .color_analysis import parse_hex,rgb_lab,REGIONS
from .vision_analysis import COMPOSITIONS,COUNTS


def analysis_permission_sql(alias='e'):
    from .library import asset_permission,permission_sql,NEEDS_REVIEW,REVIEW
    local=permission_sql("'local'","'asset:'||e.id",'local_analysis')
    asset=asset_permission('local_analysis').replace('NOT '+NEEDS_REVIEW,'(NOT '+NEEDS_REVIEW+' AND '+local+')')
    web=permission_sql('e.source_id','e.id','local_analysis')
    value=f"(CASE WHEN e.kind='asset' THEN {asset} WHEN e.kind='commons' THEN ({REVIEW} AND {web}) ELSE {web} END)"
    return value.replace('e.',alias+'.') if alias!='e' else value


@dataclass(frozen=True)
class AnalysisFilter:
    color: str|None=None
    tolerance: float=15.
    min_share: float=0.
    max_share: float=100.
    region: str='whole'
    composition: str|None=None
    count_class: str|None=None
    min_mp: float=0.
    orientation: str='all'
    include_unknown: bool=False

    def __post_init__(self):
        if self.color is not None:parse_hex(self.color)
        for key,lo,hi in [('tolerance',0,100),('min_share',0,100),('max_share',0,100),('min_mp',0,1000)]:
            value=getattr(self,key)
            if not isinstance(value,(int,float)) or not math.isfinite(value) or not lo<=value<=hi:raise ValueError('필터 수치 범위를 확인하세요.')
        if self.min_share>self.max_share:raise ValueError('최소 점유율은 최대 점유율 이하여야 합니다.')
        if self.region not in REGIONS or self.composition not in (None,*COMPOSITIONS) or self.count_class not in (None,*COUNTS) or self.orientation not in ('all','portrait','landscape','square'):raise ValueError('지원하지 않는 분석 필터입니다.')
        if type(self.include_unknown) is not bool:raise ValueError('미분석 포함 설정을 확인하세요.')
    @property
    def active(self):return bool(self.color or self.composition or self.count_class or self.min_mp or self.orientation!='all')
    def dictionary(self):return asdict(self)
    def prefix(self):
        if not self.color:return '',[]
        l,a,b=rgb_lab(parse_hex(self.color));d=self.tolerance;epsilon=.001
        sql="WITH color_hits AS MATERIALIZED (SELECT c.entry_id,100.0*SUM(c.weight)/MAX(c.total) AS share FROM color_space s JOIN analysis_colors c ON c.id=s.id JOIN image_analysis x ON x.entry_id=c.entry_id JOIN gallery_items e ON e.id=x.entry_id WHERE s.l0<=? AND s.l1>=? AND s.a0<=? AND s.a1>=? AND s.b0<=? AND s.b1>=? AND c.region=? AND ((c.l-?)*(c.l-?)+(c.a-?)*(c.a-?)+(c.b-?)*(c.b-?))<=? AND x.sha256=e.sha256 AND x.stale=0 AND "+analysis_permission_sql()+" GROUP BY c.entry_id) "
        return sql,[l+d+epsilon,l-d-epsilon,a+d+epsilon,a-d-epsilon,b+d+epsilon,b-d-epsilon,self.region,l,l,a,a,b,b,d*d+1e-9]
    def clauses(self):
        clauses=[];args=[]
        valid="EXISTS(SELECT 1 FROM image_analysis x WHERE x.entry_id=e.id AND x.sha256=e.sha256 AND x.stale=0 AND "+analysis_permission_sql()+')'
        if self.color:
            matched='EXISTS(SELECT 1 FROM color_hits h WHERE h.entry_id=e.id AND h.share>=? AND h.share<=?)'
            # No matching bin means a known 0%, not unknown. Explicitly include
            # zero-occupancy results when a user's chosen range contains zero.
            if self.min_share==0:matched=f'({matched} OR ({valid} AND EXISTS(SELECT 1 FROM analysis_colors c WHERE c.entry_id=e.id AND c.region=?) AND NOT EXISTS(SELECT 1 FROM color_hits h WHERE h.entry_id=e.id)))';args.extend((self.min_share,self.max_share,self.region))
            else:args.extend((self.min_share,self.max_share))
            if self.include_unknown:matched=f'({matched} OR NOT {valid} OR NOT EXISTS(SELECT 1 FROM analysis_colors c WHERE c.entry_id=e.id AND c.region=?))';args.append(self.region)
            clauses.append(matched)
        for field,value in [('composition',self.composition),('count_class',self.count_class)]:
            if value is None:continue
            effective=f"COALESCE((SELECT o.{field} FROM analysis_overrides o WHERE o.entry_id=e.id AND o.sha256=e.sha256),(SELECT x.{field} FROM image_analysis x WHERE x.entry_id=e.id AND x.sha256=e.sha256 AND x.stale=0 AND {analysis_permission_sql()}),'unknown')"
            condition=effective+'=?';args.append(value)
            if self.include_unknown:condition='('+condition+" OR "+effective+"='unknown')"
            clauses.append(condition)
        if self.min_mp:clauses.append('(e.width*e.height)>=?');args.append(self.min_mp*1_000_000)
        if self.orientation!='all':
            clauses.append('e.width>0 AND e.height>0')
            clauses.append({'portrait':'1.0*e.width/e.height<.9','landscape':'1.0*e.width/e.height>1.1','square':'1.0*e.width/e.height BETWEEN .9 AND 1.1'}[self.orientation])
        return clauses,args
