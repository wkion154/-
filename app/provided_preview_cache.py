"""Small persistent display cache, independent of full-file/viewer verification."""
import uuid
from pathlib import Path
from .images import decode_preview,file_hash
from .providers.http import Cancelled,RemoteError
from .provided_files import resolve,source_url


def preview(web,identity,cancelled):
    repo=web.repo;resource='web:'+identity
    item=repo.image(identity);url=source_url(item)
    def permitted():
        if cancelled.is_set():raise Cancelled()
        current=repo.image(identity)
        repo.require(current['source_id'],'thumbnail',resource)
        repo.require(current['source_id'],'original_download',resource)
        if current['url']!=item['url'] or source_url(current)!=url:
            raise RemoteError('미리보기 생성 중 파일 주소가 변경됐습니다.',blocked=True)
    permitted()
    with web.db.connect() as connection:
        cached=connection.execute('SELECT * FROM provided_preview_cache WHERE item_id=?',(identity,)).fetchone()
    if cached and cached['source_url']==url and cached['preview_url']==item['url']:
        path=Path(cached['path'])
        if path.is_file():
            if file_hash(path)!=cached['sha256']:raise RemoteError('미리보기 캐시가 변경됐습니다. 덮어쓰지 않았습니다.')
            image=decode_preview(path,512);permitted()
            return image
    version=resolve(web,identity,cancelled)
    image=decode_preview(version['path'],512)
    permitted()
    folder=web.settings.cache/'provided-previews';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(uuid.uuid4().hex+'.jpg');committed=False
    try:
        if not image.save(str(path),'JPEG',88):raise RemoteError('미리보기 캐시를 저장하지 못했습니다.')
        digest=file_hash(path);permitted()
        with web.db.connect() as connection:
            connection.execute('INSERT INTO provided_preview_cache VALUES(?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET source_url=excluded.source_url,preview_url=excluded.preview_url,path=excluded.path,sha256=excluded.sha256,provided_sha256=excluded.provided_sha256',
                               (identity,url,item['url'],str(path),digest,version['sha256']))
        committed=True
        return image
    finally:
        if not committed:path.unlink(missing_ok=True)
