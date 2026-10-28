"""Synthetic indexed palettes at 10k/100k; not actual vision accuracy or photo I/O."""
import os,sys,tempfile,time,json,threading,resource
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.database import Database
from app.source_repository import SourceRepository
from app.color_analysis import rgb_lab
from app.analysis_filters import AnalysisFilter
from app.library import LibraryRepository
colors=['#ff0000','#0000ff','#00ff00','#ffffff','#ffff00','#00ffff','#ff00ff','#000000']
results=[]
with tempfile.TemporaryDirectory() as folder:
    db=Database(Path(folder)/'library.db');sources=SourceRepository(db)
    sources.grant('local','local_analysis','allowed',{'reference':'fixture://owned','note':'Precomputed owned synthetic palettes; no pixel or real model accuracy claim.'})
    previous=0
    for count in (10000,100000):
        start=time.perf_counter()
        with db.connect() as c:
            for i in range(previous,count):
                identity=f'{i:08}';sha=f'{i:064x}'
                c.execute('INSERT INTO assets(id,path,name,sha256,width,height,size,format,registered) VALUES(?,?,?,?,?,?,?,?,?)',(identity,'/fixture-not-decoded.png',identity,sha,800,1200,1000,'PNG',i))
                c.execute("INSERT INTO resource_permissions(resource_id,operation,state,evidence,checked) VALUES(?,'local_analysis','allowed','{}',0)",('asset:'+identity,))
                c.execute("INSERT INTO image_analysis VALUES(?,?, 'fixture-palette-v1','fixture','unknown',0,'unknown',NULL,'unavailable','{}',0,0)",(identity,sha))
                for j in range(4):
                    hex=colors[(i+j)%8];lab=rgb_lab(tuple(int(hex[k:k+2],16) for k in (1,3,5)))
                    c.execute("INSERT INTO analysis_colors(entry_id,region,hex,l,a,b,weight,total) VALUES(?,'whole',?,?,?,?,?,100)",(identity,hex,*lab,(j+1)*10))
        previous=count;build=(time.perf_counter()-start)*1000
        repo=LibraryRepository(db,filters=AnalysisFilter(color='#ff0000',tolerance=1,min_share=20))
        start=time.perf_counter();rows,total=repo.query(sort='color_share');elapsed=(time.perf_counter()-start)*1000
        assert total==count*3//8 and len(rows)==256
        assert rows[0]['color_share']==40
        results.append(dict(metadata_count=count,color_bins=count*4,incremental_build_ms=round(build,2),first_page_with_count_ms=round(elapsed,2),matched=total,returned=len(rows),peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2)))
print(json.dumps(dict(scope='Linux SQLite, four precomputed synthetic colors per photo. Query includes current per-photo analysis permission, color-range aggregation, count and descending occupancy. No file decoding, actual model accuracy or physical display FPS measured.',results=results),indent=2))
