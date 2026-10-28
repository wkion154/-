"""Reproducible synthetic metadata benchmark; never represents real-photo decode speed."""
import json,platform,sqlite3,statistics,tempfile,time,tracemalloc,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.database import Database

def run():
    results=[]
    tracemalloc.start()
    with tempfile.TemporaryDirectory() as folder:
        db=Database(Path(folder)/'library.db')
        count=0
        for target in (100,1000,10000):
            with db.connect() as conn:
                conn.executemany('INSERT INTO assets(id,path,name,sha256,width,height,size,format,registered) VALUES(?,?,?,?,?,?,?,?,?)',[(str(i),'synthetic-not-a-file',f'photo-{i:05}.png',f'{i:064x}',4000,6000,1000000,'PNG',float(i)) for i in range(count,target)])
            count=target
            times={}
            for label,kwargs in [('first_page',{}),('last_page',{'offset':max(0,target-256)}),('name_search',{'search':'photo-000'}),('resolution_sort',{'sort':'resolution'})]:
                samples=[]
                for _ in range(10):
                    begin=time.perf_counter()
                    rows,total=db.query(**kwargs)
                    samples.append((time.perf_counter()-begin)*1000)
                    assert total>=len(rows)
                times[label]={'median_ms':round(statistics.median(samples),3),'max_ms':round(max(samples),3)}
            results.append({'metadata_count':target,'queries':times,'python_peak_mib':round(tracemalloc.get_traced_memory()[1]/1024**2,2)})
    return {'python':platform.python_version(),'os':platform.platform(),'sqlite':sqlite3.sqlite_version,'scope':'Synthetic metadata only; warm filesystem cache, local machine. No Windows/GPU or real-image scrolling performance guarantee.','results':results}

if __name__=='__main__':
    print(json.dumps(run(),indent=2))
