"""Compare old full-file warm previews with small cached previews; no network."""
import io,json,statistics,sys,tempfile,threading,time
from pathlib import Path
from urllib.parse import quote
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.core import Settings
from app.database import Database
from app.online_service import OnlineService
from app.providers.kpopping_browser import KpoppingBrowserProvider,OBSERVED_PAGES
from app.page_discovery import ParsedPage,ImageCandidate
from app.provided_files import resolve
from app.images import decode_preview


def main():
    output=Path(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix='kidol-preview-profile-') as directory:
        root=Path(directory);online=OnlineService(Database(root/'db.sqlite3'),Settings(root/'user'),None)
        provider=KpoppingBrowserProvider(online);repo=online.web.repo
        cdn='https://cdn.kpopping.com/kpics/generated-benchmark.png'
        wrapper='https://kpopping.com/api/kpics-image?url='+quote(cdn,safe='')+'&size=thumb'
        page=repo.add_page(provider.name,OBSERVED_PAGES['장원영'])
        repo.index_page(repo.page(page),ParsedPage('Synthetic benchmark',[ImageCandidate(wrapper,'generated','Generated','thumbnail')]))
        identity=repo.images(page_id=page)[0]['id'];evidence=dict(reference='fixture://generated',note='Synthetic benchmark only')
        repo.grant_many(provider.name,dict(metadata_search='allowed',thumbnail='allowed',original_download='allowed'),evidence,enabled=True)
        for operation in ('thumbnail','original_download'):repo.grant(provider.name,operation,'allowed',evidence,'web:'+identity)
        buffer=io.BytesIO();Image.effect_noise((3000,4000),70).convert('RGB').save(buffer,'PNG');data=buffer.getvalue()
        class Response(io.BytesIO):
            def __init__(self,body,mime):super().__init__(body);self.headers={'Content-Type':mime,'Content-Length':str(len(body))}
        class Client:
            allowed_hosts=repo.spec(provider.name).domains
            def open(self,url,cancelled,accept=None):
                if url.endswith('/robots.txt'):return Response(b'User-agent: *\nAllow: /','text/plain')
                assert url==cdn,'No network is permitted in this benchmark'
                return Response(data,'image/png')
        online.web.http_factory=lambda _:Client()
        online.settings.values['kpopping_provided_preview']=True;cancel=threading.Event()
        online.web.preview(identity,cancel)
        def old():
            version=resolve(online.web,identity,cancel);decode_preview(version['path'],512)
        def new():online.web.preview(identity,cancel)
        def measure(function):
            values=[]
            for _ in range(7):
                start=time.perf_counter();function();values.append(time.perf_counter()-start)
            return statistics.median(values)
        before=measure(old);after=measure(new)
        with online.db.connect() as connection:path=connection.execute('SELECT path FROM provided_preview_cache WHERE item_id=?',(identity,)).fetchone()[0]
        report=dict(scope='Linux synthetic 12MP PNG, warm cache, no network/Windows timing',
                    source_bytes=len(data),preview_bytes=Path(path).stat().st_size,
                    old_median_seconds=before,new_median_seconds=after,ratio=before/after,repeats=7)
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
