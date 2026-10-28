"""Indexed local/remote gallery; metadata rows never masquerade as local files."""
from pathlib import Path
import json
import time
import uuid
from PIL import Image
from .providers.http import RemoteError,Cancelled
from .online_service import download_file
from .images import file_hash,inspect_image,decode_preview


def remote(row):return row.get('kind','asset')!='asset'


def permission_sql(source,resource,operation):
    return f"EXISTS(SELECT 1 FROM source_profiles p JOIN source_permissions s ON s.source_id=p.id AND s.profile_digest=p.digest JOIN resource_permissions r ON r.operation=s.operation WHERE p.id={source} AND p.enabled=1 AND p.adapter<>'candidate' AND s.operation='{operation}' AND s.state='allowed' AND r.resource_id={resource} AND r.state='allowed')"


REVIEW="EXISTS(SELECT 1 FROM provider_reviews WHERE provider='wikimedia' AND enabled=1 AND reviewed_at IS NOT NULL AND evidence<>'')"
NEEDS_REVIEW="(EXISTS(SELECT 1 FROM asset_sources o WHERE o.asset_id=e.id AND o.source_id<>'local') OR EXISTS(SELECT 1 FROM remote_images c WHERE c.local_asset_id=e.id))"


def asset_permission(operation):
    web=permission_sql('w.source_id',"'web:'||w.id",operation)
    commons=permission_sql("'wikimedia'","'commons:'||c.id",operation)
    return f"(NOT {NEEDS_REVIEW} OR EXISTS(SELECT 1 FROM web_images w WHERE w.local_asset_id=e.id AND w.sha256=e.sha256 AND {web}) OR EXISTS(SELECT 1 FROM remote_images c WHERE c.local_asset_id=e.id AND c.license_status='eligible' AND {REVIEW} AND {commons}))"


def person_links(entry='e.id'):
    return f"(SELECT person_id FROM image_person WHERE image_id={entry} AND status IN ('user_confirmed','confirmed') UNION SELECT person_id FROM gallery_people WHERE entry_id={entry})"


def group_links(entry='e.id'):
    return f"(SELECT group_id FROM image_group WHERE image_id={entry} UNION SELECT group_id FROM gallery_groups WHERE entry_id={entry} UNION SELECT t.entity_id FROM remote_targets rt JOIN watch_targets t ON t.id=rt.target_id WHERE 'commons:'||rt.remote_id={entry} AND t.kind='group')"


class LibraryRepository:
    MODES=('all','local','online')
    def __init__(self,db,mode='all',filters=None):
        if mode not in self.MODES:raise ValueError('지원하지 않는 라이브러리 보기입니다.')
        self.db,self.mode=db,mode
        from .analysis_filters import AnalysisFilter
        self.filters=filters or AnalysisFilter()

    def version(self):
        with self.db.connect() as c:return c.execute('SELECT version FROM gallery_version WHERE id=1').fetchone()[0]

    def _selection(self,scope,search,sort,source_filter):
        mode='local' if scope and scope[0]=='local' else self.mode
        where=["(e.kind='asset' OR e.linked_asset_id IS NULL)"]
        args=[]
        if mode=='local':where.append("e.kind='asset'")
        elif mode=='online':where.append("e.kind<>'asset'")
        people=person_links();groups=group_links()
        if not scope or scope[0] not in ('local','favorite'):
            where.append("e.hidden=0 AND e.id NOT IN (SELECT v.asset_id FROM asset_visibility v WHERE v.hidden=1 AND EXISTS(SELECT 1 FROM image_person ip JOIN persons p ON p.id=ip.person_id WHERE ip.image_id=v.asset_id AND p.gender='male') AND NOT EXISTS(SELECT 1 FROM image_person ip JOIN persons p ON p.id=ip.person_id WHERE ip.image_id=v.asset_id AND p.gender<>'male') AND NOT EXISTS(SELECT 1 FROM asset_sources s WHERE s.asset_id=v.asset_id AND s.source_id='local'))")
        if scope:
            kind,value=scope
            if kind=='favorite':where.append('e.favorite=1')
            elif kind=='person':where.append(f'? IN {people}');args.append(value)
            elif kind=='solo':where.append(f'EXISTS(SELECT 1 FROM persons p WHERE p.id IN {people} AND p.solo=1)')
            elif kind=='group':
                condition='' if value is None else ' AND g.group_id=?'
                membership='' if value is None else ' AND m.group_id=?'
                where.append(f'(EXISTS(SELECT 1 FROM (SELECT group_id FROM image_group WHERE image_id=e.id UNION SELECT group_id FROM gallery_groups WHERE entry_id=e.id UNION SELECT t.entity_id FROM remote_targets rt JOIN watch_targets t ON t.id=rt.target_id WHERE e.id=\'commons:\'||rt.remote_id AND t.kind=\'group\') g WHERE 1{condition}) OR EXISTS(SELECT 1 FROM memberships m WHERE m.person_id IN {people} AND m.gallery_target=1{membership}))')
                if value is not None:args.extend((value,value))
        if source_filter:
            kind,value=source_filter
            if kind=='site':
                where.append("(e.source_id=? AND e.kind<>'asset' OR EXISTS(SELECT 1 FROM asset_sources s WHERE s.asset_id=e.id AND s.source_id=?) OR (?='wikimedia' AND EXISTS(SELECT 1 FROM remote_images r WHERE r.local_asset_id=e.id)))");args.extend((value,value,value))
            elif kind=='category':
                where.append("(EXISTS(SELECT 1 FROM source_profiles p WHERE p.id=e.source_id AND e.kind<>'asset' AND p.category=?) OR EXISTS(SELECT 1 FROM asset_sources s JOIN source_profiles p ON p.id=s.source_id WHERE s.asset_id=e.id AND p.category=?) OR (?='user' AND EXISTS(SELECT 1 FROM asset_sources s WHERE s.asset_id=e.id AND s.source_id='local')) OR (?='fan' AND EXISTS(SELECT 1 FROM remote_images r WHERE r.local_asset_id=e.id)))");args.extend((value,)*4)
        if search.strip():
            term='%'+search.strip().replace('!','!!').replace('%','!%').replace('_','!_')+'%'
            where.append(f"(e.name LIKE ? ESCAPE '!' OR EXISTS(SELECT 1 FROM persons p WHERE p.id IN {people} AND (p.name LIKE ? ESCAPE '!' OR p.aliases LIKE ? ESCAPE '!')) OR EXISTS(SELECT 1 FROM groups g WHERE g.id IN {groups} AND (g.name LIKE ? ESCAPE '!' OR g.aliases LIKE ? ESCAPE '!')) OR EXISTS(SELECT 1 FROM memberships m JOIN groups g ON g.id=m.group_id WHERE m.person_id IN {people} AND (g.name LIKE ? ESCAPE '!' OR g.aliases LIKE ? ESCAPE '!')))")
            args.extend((term,)*7)
        extra,extra_args=self.filters.clauses();where.extend(extra);args.extend(extra_args)
        orders={'newest':'e.registered DESC,e.id','oldest':'e.registered,e.id','name':'e.name COLLATE NOCASE,e.id','resolution':'(e.width*e.height) DESC,e.id'}
        order=orders.get(sort,orders['newest'])
        if sort=='color_share' and self.filters.color:order='COALESCE((SELECT share FROM color_hits h WHERE h.entry_id=e.id),-1) DESC,e.registered DESC,e.id'
        return ' WHERE '+' AND '.join(where),args,order

    def query(self,scope=None,search='',sort='newest',offset=0,limit=256,count=True,source_filter=None,cancelled=None):
        prefix,prefix_args=self.filters.prefix()
        clause,args,order=self._selection(scope,search,sort,source_filter)
        order_index={'newest':'gallery_newest','oldest':'gallery_oldest','name':'gallery_name','resolution':'gallery_pixels'}.get(sort,'gallery_newest')
        permitted=permission_sql('e.source_id','e.id','thumbnail')
        thumbnail=f"CASE WHEN e.kind='asset' THEN {asset_permission('thumbnail')} WHEN e.kind='commons' THEN ({permitted} AND {REVIEW} AND e.license_status='eligible' AND e.thumbnail_url IS NOT NULL) ELSE ({permitted} AND (e.has_preview=1 OR e.role='thumbnail')) END"
        from .analysis_filters import analysis_permission_sql
        known="EXISTS(SELECT 1 FROM image_analysis x WHERE x.entry_id=e.id AND x.sha256=e.sha256 AND x.stale=0 AND "+analysis_permission_sql()+") AND EXISTS(SELECT 1 FROM analysis_colors c WHERE c.entry_id=e.id AND c.region='"+self.filters.region+"')"
        share="COALESCE((SELECT share FROM color_hits h WHERE h.entry_id=e.id),CASE WHEN "+known+" THEN 0 ELSE NULL END)" if self.filters.color else 'NULL'
        with self.db.connect() as c:
            if cancelled is not None:c.set_progress_handler(lambda:int(cancelled.is_set()),1000)
            total=c.execute(prefix+'SELECT COUNT(*) FROM gallery_items e INDEXED BY gallery_visibility'+clause,(*prefix_args,*args)).fetchone()[0] if count else None
            rows=[dict(r) for r in c.execute(prefix+f"SELECT {share} AS color_share,e.*,COALESCE(a.path,'') AS path,COALESCE(a.managed,0) AS managed,{NEEDS_REVIEW} AS requires_review,{thumbnail} AS thumbnail_allowed FROM (SELECT e.* FROM gallery_items e INDEXED BY "+order_index+clause+" ORDER BY "+order+" LIMIT ? OFFSET ?) e LEFT JOIN assets a ON e.kind='asset' AND a.id=e.id ORDER BY "+order,(*prefix_args,*args,limit,offset))]
        for row in rows:
            row['thumbnail_key']=row['sha256'] if row['kind']=='asset' else row['id']+'@'+str(row['sha256'] or row['image_url'])
        return rows,total

    def locate(self,identity,scope=None,search='',sort='newest',source_filter=None):
        prefix,prefix_args=self.filters.prefix()
        clause,args,order=self._selection(scope,search,sort,source_filter)
        order_index={'newest':'gallery_newest','oldest':'gallery_oldest','name':'gallery_name','resolution':'gallery_pixels'}.get(sort,'gallery_newest')
        with self.db.connect() as c:
            row=c.execute('SELECT linked_asset_id,sha256,kind FROM gallery_items WHERE id=?',(identity,)).fetchone()
            if row and row['kind']!='asset':
                asset=c.execute('SELECT id FROM assets WHERE id=?',(row['linked_asset_id'],)).fetchone()
                if asset:identity=asset['id']
            result=c.execute(prefix+'SELECT position FROM (SELECT e.id,ROW_NUMBER() OVER (ORDER BY '+order+')-1 AS position FROM gallery_items e INDEXED BY '+order_index+clause+') WHERE id=?',(*prefix_args,*args,identity)).fetchone()
            return result['position'] if result else None

    def entry(self,identity):
        with self.db.connect() as c:
            row=c.execute(f"SELECT e.*,COALESCE(a.path,'') AS path,COALESCE(a.managed,0) AS managed,{NEEDS_REVIEW} AS requires_review FROM gallery_items e LEFT JOIN assets a ON e.kind='asset' AND a.id=e.id WHERE e.id=?",(identity,)).fetchone()
            if row is None:raise ValueError('라이브러리 항목을 찾을 수 없습니다.')
            return dict(row)

    def favorite(self,identity,value):
        row=self.entry(identity)
        if row['kind']=='asset':return self.db.favorite(identity,value)
        with self.db.connect() as c:
            c.execute('INSERT INTO gallery_favorites VALUES(?,?) ON CONFLICT(entry_id) DO UPDATE SET favorite=excluded.favorite',(identity,int(value)))

    def associations(self,identity):
        row=self.entry(identity)
        if row['kind']=='asset':return self.db.associations(identity)
        with self.db.connect() as c:
            return ([dict(r) for r in c.execute('SELECT p.* FROM persons p JOIN gallery_people g ON g.person_id=p.id WHERE g.entry_id=?',(identity,))],
                    [dict(r) for r in c.execute('SELECT p.* FROM groups p JOIN gallery_groups g ON g.group_id=p.id WHERE g.entry_id=?',(identity,))])

    def associate(self,identity,people,groups):
        row=self.entry(identity)
        if row['kind']=='asset':raise ValueError('로컬 사진 연결 관리에서 수정하세요.')
        if len(people)>100 or len(groups)>100:raise ValueError('한 번에 연결할 항목 수를 초과했습니다.')
        with self.db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM gallery_people WHERE entry_id=?',(identity,));c.execute('DELETE FROM gallery_groups WHERE entry_id=?',(identity,))
            for person in set(people):c.execute('INSERT INTO gallery_people VALUES(?,?)',(identity,person))
            for group in set(groups):c.execute('INSERT INTO gallery_groups VALUES(?,?)',(identity,group))
            # If a job promoted the item while its editor was open, preserve these
            # explicit additions on the saved asset as well; never remove local links.
            linked=c.execute('SELECT linked_asset_id FROM gallery_items WHERE id=?',(identity,)).fetchone()[0]
            if linked:
                for person in set(people):c.execute('INSERT OR IGNORE INTO image_person(image_id,person_id) VALUES(?,?)',(linked,person))
                for group in set(groups):c.execute('INSERT OR IGNORE INTO image_group VALUES(?,?)',(linked,group))

    def details(self,identity):
        row=self.entry(identity);people,groups=self.associations(identity)
        return row,people,groups


class LibraryService:
    def __init__(self,online):
        self.online=online;self.db=online.db;self.settings=online.settings
        self.repo=LibraryRepository(self.db)

    def _asset_allowed(self,row,operation):
        with self.db.connect() as c:
            return c.execute('SELECT 1 FROM gallery_items e WHERE e.id=? AND '+asset_permission(operation),(row['id'],)).fetchone() is not None

    def preview(self,row,cancelled):
        if row.get('kind','asset')=='asset':
            from .images import make_thumbnail
            if not self._asset_allowed(row,'thumbnail'):raise RemoteError('저장된 사진의 표시 권한을 확인하세요.',blocked=True)
            return decode_preview(make_thumbnail(row['path'],self.settings.cache,row['sha256']),512)
        if row['kind']=='web':return self.online.web.preview(row['ref_id'],cancelled)
        return self.online.thumbnail(row['ref_id'],cancelled)

    def original(self,row,cancelled):
        current=row|self.repo.entry(row['id'])
        if current['kind']=='asset':
            if not self._asset_allowed(current,'original_download'):raise RemoteError('저장된 사진의 원본 보기 권한을 확인하세요.',blocked=True)
            if not Path(current['path']).is_file():raise RemoteError('원본 경로를 확인하거나 다시 연결하세요.')
            return current
        if current['kind']=='web':
            item=self.online.web.repo.image(current['ref_id'])
            self.online.web.repo.require(item['source_id'],'original_download',current['id'])
            cached=Path(item['cache_path']) if item['cache_path'] else None
            if cached is None or not cached.is_file():self.online.web.probe(item['id'],cancelled)
            item=self.online.web.repo.image(item['id']);self.online.web.repo.require(item['source_id'],'original_download',current['id'])
            if file_hash(item['cache_path'])!=item['sha256']:raise RemoteError('검사 캐시가 변경됐습니다. 다시 실측 검사하세요.')
            return current|dict(path=item['cache_path'],sha256=item['sha256'],width=item['width'],height=item['height'],size=item['size'],format=item['mime'],managed=0)
        item=self.online.repo.image(current['ref_id'])
        self.online.require_review('original_download',current['id'])
        if item['license_status']!='eligible':raise RemoteError('사진별 이용 조건 확인이 필요합니다.',blocked=True)
        revision=(item['source_sha1'] or '')+'|'+item['original_url']
        with self.db.connect() as c:cache=c.execute('SELECT * FROM remote_view_cache WHERE entry_id=?',(current['id'],)).fetchone()
        if cache and cache['source_revision']==revision and Path(cache['path']).is_file():
            if file_hash(cache['path'])!=cache['sha256']:raise RemoteError('보기 캐시가 변경됐습니다. 덮어쓰지 않았습니다.')
            self.online.require_review('original_download',current['id'])
            return current|{k:cache[k] for k in ('path','sha256','width','height','size','format')}
        suffix={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}.get(item['mime'])
        if not suffix:raise RemoteError('이 파일 형식은 원본 보기를 지원하지 않습니다.')
        destination=self.settings.cache/'original-view'/('commons-'+item['id']+'-'+uuid.uuid4().hex+suffix)
        download_file(self.online.provider.http,item['original_url'],destination,cancelled,expected_size=item['size'],expected_sha1=item['source_sha1'])
        meta=inspect_image(destination)
        fresh=self.online.repo.image(item['id']);self.online.require_review('original_download',current['id'])
        if (fresh['source_sha1'] or '')+'|'+fresh['original_url']!=revision:raise RemoteError('보기 중 원격 개정본이 변경됐습니다.')
        with self.db.connect() as c:
            c.execute('INSERT INTO remote_view_cache VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET path=excluded.path,sha256=excluded.sha256,width=excluded.width,height=excluded.height,size=excluded.size,format=excluded.format,source_revision=excluded.source_revision,verified=excluded.verified',
                      (current['id'],str(destination),meta['sha256'],meta['width'],meta['height'],meta['size'],meta['format'],revision,time.time()))
        return current|{k:meta[k] for k in ('path','sha256','width','height','size','format')}

    def preserve(self,row):
        current=self.repo.entry(row['id'])
        if current['kind']=='asset':raise ValueError('이미 로컬 라이브러리에 등록된 사진입니다.')
        if current['kind']=='web':
            people,_=self.repo.associations(current['id'])
            return self.online.repo.enqueue('web_download',{'item_id':current['ref_id'],'confirmed_people':[p['id'] for p in people]},force=True)
        with self.db.connect() as c:
            target=c.execute("SELECT t.id FROM remote_targets rt JOIN watch_targets t ON t.id=rt.target_id WHERE rt.remote_id=? AND t.kind='group' LIMIT 1",(current['ref_id'],)).fetchone()
        return self.online.repo.enqueue('download',{'remote_id':current['ref_id'],'target_id':target['id'] if target else None},force=True)
