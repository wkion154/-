import sys
from pathlib import Path
if not getattr(sys,"frozen",False):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.main import main
if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--smoke-test':
        from app.release_smoke import run
        raise SystemExit(run(sys.argv[2]))
    raise SystemExit(main())
