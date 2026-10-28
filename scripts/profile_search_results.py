"""Offline synthetic gallery benchmark; no network or user library access."""
import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.database import Database
from app.core import Settings
from app.online_service import OnlineService
from app.search_service import SearchService
from app.analysis_filters import AnalysisFilter
from app.library import LibraryRepository
from app.page_discovery import ImageCandidate,ParsedPage


def profile():
    with tempfile.TemporaryDirectory(prefix='kidol-search-profile-') as directory:
        root=Path(directory);db=Database(root/'db')
        online=OnlineService(db,Settings(root/'user'),None);repo=online.web.repo
        repo.save_spec(dict(id='bench',name='Synthetic benchmark',category='user',adapter='html',domains=['owned.example']))
        page=repo.add_page('bench','https://owned.example/fixture')
        repo.index_page(repo.page(page),ParsedPage('Synthetic',[
            ImageCandidate(f'https://owned.example/{i}.jpg',str(i)) for i in range(10000)]))
        search=SearchService(online,[]);session=search.start('fixture',AnalysisFilter())
        rows=repo.images(page_id=page,limit=1000)
        with db.connect() as connection:
            connection.executemany('INSERT INTO search_hits VALUES(?,?,?)',[
                (session,'web:'+row['id'],i) for i,row in enumerate(rows)])
        repository=LibraryRepository(db);times=[]
        for _ in range(5):
            start=time.perf_counter();result=repository.query(scope=('search',session),sort='discovery')
            times.append(time.perf_counter()-start)
            assert result[1]==1000 and len(result[0])==256
        return dict(synthetic=True,network_used=False,gallery_items=10000,search_hits=1000,
                    returned_rows=256,runs_seconds=times,median_seconds=statistics.median(times),
                    scope='SQLite metadata count + first page only; excludes network, thumbnail decode and Windows UI')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    report=json.dumps(profile(),ensure_ascii=False,indent=2)
    if args.output:args.output.write_text(report+'\n','utf-8')
    print(report)
