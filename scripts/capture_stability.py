"""Job/source UI with owned fixture records; never represents real-site success."""
import os,sys,tempfile,time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from app.database import Database
from app.core import Settings
from app.source_repository import SourceRepository
from app.online_service import OnlineService
from app.online_runner import OnlineRunner
from app.online_dialog import OnlineDialog
from app.theme import STYLE

app=QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);db=Database(root/'library.db');settings=Settings(root/'user');settings.values['online_enabled']=False
    service=OnlineService(db,settings,None);sources=SourceRepository(db)
    for identity,name,state,note in [('fixture-a','합성 출처 A · 실사이트 아님','done','합성 페이지 처리 검증 완료'),('fixture-b','합성 출처 B · 실사이트 아님','retry','모의 HTTP 429 · 출처별 재시도 대기'),('fixture-c','합성 출처 C · 실사이트 아님','blocked','연결/이용 조건 확인 필요')]:
        sources.save_spec(dict(id=identity,name=name,category='user',adapter='html',domains=[identity+'.example']))
        page=sources.add_page(identity,'https://'+identity+'.example/page')
        job=service.repo.enqueue('web_scan',{'page_id':page});claim=service.repo.claim(identity=job)
        service.repo.finish(job,state,note,delay=90 if state=='retry' else 0,claim_token=claim['claim_token'])
    runner=OnlineRunner(service,settings);runner.timer.stop()
    dialog=OnlineDialog(service,runner,settings);dialog.resize(1240,820);dialog.show();dialog.tabs.setCurrentWidget(dialog.jobs)
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        app.processEvents()
        if not dialog.snapshot_pending and dialog.jobs.rowCount()==3:break
        time.sleep(.005)
    assert dialog.jobs.rowCount()==3
    dialog.grab().save('docs/stability-ui-v0.6.png')
    dialog.close();runner.stop()
print('Synthetic source-isolated job status capture saved.')
