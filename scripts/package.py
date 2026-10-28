import argparse,hashlib,zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--windows',action='store_true')
args=parser.parse_args()
output=root/'dist';output.mkdir(exist_ok=True)
if args.windows:
    source=output/'K-IDOL-ARCHIVE'
    if not (source/'K-IDOL-ARCHIVE.exe').is_file():raise SystemExit('Windows executable not found; build on Windows first.')
    destination=output/'K-IDOL-ARCHIVE_v0.1.0_Windows.zip'
    files=[p for p in source.rglob('*') if p.is_file()]
else:
    source=root
    destination=output/'K-IDOL-ARCHIVE_v0.1.0_Source.zip'
    excluded={'.venv','.git','dist','build','__pycache__','.pytest_cache'}
    files=[p for p in source.rglob('*') if p.is_file() and not excluded.intersection(p.relative_to(source).parts)]
with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
    for path in files:archive.write(path,Path('K-IDOL-ARCHIVE')/path.relative_to(source))
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
checksum=hashlib.sha256(destination.read_bytes()).hexdigest()
destination.with_suffix('.zip.sha256').write_text(checksum+'  '+destination.name+'\n')
print(destination)
print(checksum)
