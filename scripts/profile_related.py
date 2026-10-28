"""Bounded metadata candidate search at 10k/100k; no real photographs or AI."""
import json,sys,tempfile,time,threading,resource
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.database import Database
from app.related import RelatedRepository
results=[]
with tempfile.TemporaryDirectory() as folder:
    db=Database(Path(folder)/'library.db');repo=RelatedRepository(db)
    person=db.add_entity('person','Owned synthetic fixture')
    previous=0
    for count in (10000,100000):
        with db.connect() as c:
            c.executemany('INSERT INTO assets(id,path,name,sha256,width,height,size,format,registered) VALUES(?,?,?,?,?,?,?,?,?)',((f'{i:08}', '/fixture/not-decoded.jpg',f'fixture-{i}',f'{i:064x}',800,1200,1000,'JPEG',i) for i in range(previous,count)))
            c.executemany('INSERT INTO image_person(image_id,person_id) VALUES(?,?)',((f'{i:08}',person) for i in range(previous,count)))
            c.executemany("INSERT INTO asset_sources(id,asset_id,source_id) VALUES(?,?,'local')",((f'local-{i}',f'{i:08}') for i in range(previous,count)))
        previous=count
        start=time.perf_counter();result=repo.search('00000000',threading.Event());elapsed=(time.perf_counter()-start)*1000
        assert len(result['rows'])<=repo.MAX_RESULTS and result['truncated']
        results.append(dict(metadata_count=count,search_ms=round(elapsed,2),returned=len(result['rows']),max_candidate_budget=repo.MAX_CANDIDATES,per_reason_budget=repo.PER_REASON,peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2)))
print(json.dumps(dict(scope='Linux SQLite worker search; generated metadata only, no image decoding, real photo relevance or physical display FPS measured.',results=results),indent=2))
