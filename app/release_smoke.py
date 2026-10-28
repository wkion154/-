"""Opt-in executable smoke check. Never opens the user's library or the network."""
import json
import tempfile
import time
from pathlib import Path


def run(report_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from PIL import Image
    from . import __version__
    from .core import Settings
    from .database import Database
    from .images import inspect_image
    from .main_window import MainWindow
    from .theme import STYLE
    import platform
    report=dict(version=__version__,platform=platform.system(),checks={},success=False)
    destination=Path(report_path).resolve();destination.parent.mkdir(parents=True,exist_ok=True)
    window=None
    try:
        app=QApplication.instance() or QApplication([]);app.setStyleSheet(STYLE)
        with tempfile.TemporaryDirectory(prefix='kidol-release-smoke-') as directory:
            root=Path(directory);settings=Settings(root/'user');settings.values.update(online_enabled=False,analysis_auto=False)
            db=Database(root/'library.db');photo=root/'배포 검증 sample.png'
            Image.new('RGB',(180,240),'#a193bc').save(photo)
            identity=db.register(inspect_image(photo))[0];db.favorite(identity,True)
            window=MainWindow(db,settings);window.show();window.online_runner.timer.stop()
            start=time.monotonic();timer=QTimer()
            def check():
                ready=window.model.total==1 and bool(window.gallery.thumbs)
                if not ready and time.monotonic()-start<20:return
                report['checks']['gallery_and_thumbnail']=ready
                timer.stop();window.close();app.quit()
            timer.timeout.connect(check);timer.start(25);app.exec()
            with db.connect() as c:
                report['checks']['sqlite_integrity']=c.execute('PRAGMA quick_check').fetchone()[0]=='ok'
                report['checks']['favorite_persisted']=bool(c.execute('SELECT favorite FROM assets WHERE id=?',(identity,)).fetchone()[0])
            report['checks']['settings_saved']=(settings.root/'settings.json').is_file()
            report['checks']['offline']=settings.values['online_enabled'] is False
            report['success']=all(report['checks'].values())
    except Exception as error:
        report['error']=str(error)
        if window is not None:window.close()
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['success'] else 1
