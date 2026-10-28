import logging
import sys
from PySide6.QtWidgets import QApplication,QMessageBox
from .core import Settings,data_root,configure_logging
from .database import Database
from .theme import STYLE

def main():
    app=QApplication(sys.argv)
    app.setApplicationName('K-IDOL ARCHIVE')
    app.setOrganizationName('K-IDOL ARCHIVE')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    root=data_root()
    try:
        settings=Settings(root)
        configure_logging(root)
        db=Database(root/'library.sqlite3')
        from .main_window import MainWindow
        window=MainWindow(db,settings)
        def exception(kind,value,trace):
            logging.error('Unhandled error',exc_info=(kind,value,trace))
            QMessageBox.warning(window,'작업 오류',str(value))
        sys.excepthook=exception
        window.show()
        return app.exec()
    except Exception as error:
        logging.exception('Startup failed')
        QMessageBox.critical(None,'라이브러리를 열 수 없습니다',str(error)+'\n사용자 데이터는 삭제하지 않았습니다.')
        return 1

if __name__=='__main__':
    raise SystemExit(main())
