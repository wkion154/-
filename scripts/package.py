import argparse,hashlib,zipfile,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(root))
from app import __version__
parser=argparse.ArgumentParser()
parser.add_argument('--windows',action='store_true')
args=parser.parse_args()
output=root/'dist';output.mkdir(exist_ok=True)
if args.windows:
    source=output/'K-IDOL-ARCHIVE'
    if not (source/'K-IDOL-ARCHIVE.exe').is_file():raise SystemExit('Windows executable not found; build on Windows first.')
    destination=output/f'K-IDOL-ARCHIVE_v{__version__}_Windows.zip'
    files=[p for p in source.rglob('*') if p.is_file()]
else:
    source=root
    destination=output/f'K-IDOL-ARCHIVE_v{__version__}_Source.zip'
    excluded={'.venv','.git','dist','build','__pycache__','.pytest_cache','credentials'}
    files=[p for p in source.rglob('*') if p.is_file() and p.suffix.lower()!='.dpapi' and not excluded.intersection(p.relative_to(source).parts)]
files=sorted(files)
if any(p.is_symlink() for p in files):raise SystemExit('Release packaging refuses symlinks.')
manifest=dict(version=__version__,kind='windows-base' if args.windows else 'source',files={})
with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in files:
        relative=path.relative_to(source).as_posix()
        data=path.read_bytes();manifest['files'][relative]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        archive.writestr('K-IDOL-ARCHIVE/'+relative,data)
    archive.writestr('K-IDOL-ARCHIVE/RELEASE-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
from verify_package import verify
verify(destination)
checksum=hashlib.sha256(destination.read_bytes()).hexdigest()
destination.with_suffix('.zip.sha256').write_text(checksum+'  '+destination.name+'\n')
print(destination)
print(checksum)
