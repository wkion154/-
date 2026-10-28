"""Permission checked, measured site-provided files for existing gallery items.

Separate revisions keep the small preview and the larger file from replacing
one another. An actual decode proves dimensions, never maximum source quality.
"""
import json
import uuid
from pathlib import Path
from PIL import Image
from .images import file_hash,inspect_image
from .providers.http import Cancelled,RemoteError,validate_url


def source_url(item):
    if item['source_id']=='dbkpop-search' and item['role']=='thumbnail':
        from .providers.dbkpop import upload_url
        try:evidence=json.loads(item['provenance'])
        except (ValueError,TypeError):return None
        if not isinstance(evidence,list):return None
        urls={upload_url(row.get('provided_resource_url')) for row in evidence if isinstance(row,dict)}-{None}
        return next(iter(urls)) if len(urls)==1 else None
    if item['source_id']!='kpopping-browser' or item['role']!='thumbnail':return None
    from .providers.kpopping import provided_file_url
    return provided_file_url(item['url'])


def resolve(web,identity,cancelled):
    repo=web.repo;item=repo.image(identity);resource='web:'+identity
    repo.require(item['source_id'],'original_download',resource)
    if cancelled.is_set():raise Cancelled()
    url=source_url(item)
    if not url:raise RemoteError('제공된 큰 파일 주소를 확인하지 못했습니다. 미리보기를 원본으로 저장하지 않습니다.',blocked=True)
    validate_url(url,repo.spec(item['source_id']).domains)

    def fresh_permission():
        if cancelled.is_set():raise Cancelled()
        fresh=repo.image(identity)
        repo.require(fresh['source_id'],'original_download',resource)
        if fresh['source_id']!=item['source_id'] or fresh['url']!=item['url'] or source_url(fresh)!=url:
            raise RemoteError('파일 확인 중 출처 주소가 변경됐습니다. 다시 요청하세요.',blocked=True)

    with web.db.connect() as connection:
        revisions=connection.execute('SELECT * FROM web_revisions WHERE item_id=? ORDER BY created DESC',(identity,)).fetchall()
    for row in revisions:
        evidence=json.loads(row['evidence'])
        if not isinstance(evidence,dict) or evidence.get('kind')!='provided_file' or evidence.get('source_url')!=url or evidence.get('preview_url')!=item['url']:continue
        path=Path(row['path'])
        if not path.is_file():continue
        if file_hash(path)!=row['sha256']:raise RemoteError('제공 파일 캐시가 변경됐습니다. 덮어쓰지 않았습니다.')
        fresh_permission()
        # Reuse measured metadata; the viewer decodes this file itself. A full
        # integrity decode on every cache hit needlessly doubles that work.
        return dict(path=str(path),sha256=row['sha256'],width=row['width'],height=row['height'],
                    size=row['size'],format=row['mime'],source_url=url,provided_file_only=True)

    # Check the actual CDN's policy as well as the listing site's policy.
    web.robots(item['source_id'],url,cancelled)
    from .online_service import download_file
    staging=web.settings.cache/'provided-files'/item['source_id']/(uuid.uuid4().hex+'.probe')
    typed=None
    try:
        download_file(web.client(item['source_id']),url,staging,cancelled,limit=64*1024*1024,max_pixels=40_000_000)
        with Image.open(staging) as image:fmt=image.format
        suffix={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}.get(fmt)
        if not suffix:raise RemoteError('제공된 파일 형식을 지원하지 않습니다.')
        typed=staging.with_suffix(suffix);staging.rename(typed)
        meta=inspect_image(typed);fresh_permission()
        destination=typed.parent/(meta['sha256']+suffix)
        if destination.exists():
            if file_hash(destination)!=meta['sha256']:raise RemoteError('기존 제공 파일 캐시가 변경됐습니다. 덮어쓰지 않았습니다.')
        else:typed.replace(destination)
        evidence=dict(kind='provided_file',source_url=url,preview_url=item['url'],original_verified=False,
                      dimensions_verified=True,permission=repo.permissions(item['source_id'],resource)['original_download'])
        # Keep the preview's measured bytes/path intact. The file is another
        # revision of this image object, not another gallery object.
        with web.db.connect() as connection:
            connection.execute('INSERT INTO web_revisions(item_id,sha256,path,width,height,mime,size,created,evidence) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(item_id,sha256) DO UPDATE SET evidence=excluded.evidence',
                               (identity,meta['sha256'],str(destination),meta['width'],meta['height'],Image.MIME.get(fmt,fmt),meta['size'],web.clock(),json.dumps(evidence,ensure_ascii=False)))
        return meta|dict(path=str(destination),source_url=url,provided_file_only=True)
    finally:
        staging.unlink(missing_ok=True)
        if typed is not None:typed.unlink(missing_ok=True)
