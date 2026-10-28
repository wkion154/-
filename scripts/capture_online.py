"""Capture the actual pending-policy state; no fabricated collection results."""
import os,sys,tempfile,time
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from app.core import Settings
from app.database import Database
from app.main_window import MainWindow
from app.online_dialog import OnlineDialog
from app.theme import STYLE
app=QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);settings=Settings(root)
    w=MainWindow(Database(root/'library.sqlite3'),settings)
    service=w.online_service
    service.repo.enqueue('discover',{})
    # Gate is intentionally unreviewed; this records a blocked job without HTTP.
    w.online_runner.tick(manual=True)
    dialog=OnlineDialog(service,w.online_runner,settings,w)
    dialog.show();dialog.tabs.setCurrentWidget(dialog.jobs)
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        app.processEvents()
        if dialog.jobs.rowCount() and not w.online_runner.running and not dialog.snapshot_pending:break
        time.sleep(.005)
    dialog.reload()
    until=time.monotonic()+.15
    while time.monotonic()<until:app.processEvents();time.sleep(.005)
    dialog.grab().save('docs/online-ui-v0.3.png')
    dialog.close();w.close()
print('Captured actual pending-policy/blocked state. No live collection data used.')
