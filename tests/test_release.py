import hashlib,json,zipfile,subprocess,sys,os
from pathlib import Path
import pytest
from scripts.verify_package import verify


def archive(path,extra=None,tampered=False):
    name='K-IDOL-ARCHIVE/readme.txt';data=b'release data'
    manifest=dict(version='test',kind='source',files={'readme.txt':dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())})
    with zipfile.ZipFile(path,'w') as z:
        z.writestr(name,b'changed' if tampered else data)
        z.writestr('K-IDOL-ARCHIVE/RELEASE-MANIFEST.json',json.dumps(manifest))
        if extra:z.writestr(extra,b'extra')


def test_archive_inventory_hash_and_unsafe_paths(tmp_path):
    path=tmp_path/'package.zip';archive(path);assert verify(path)['success']
    archive(path,tampered=True)
    with pytest.raises(ValueError,match='checksum'):verify(path)
    for extra in ['K-IDOL-ARCHIVE/../outside','K-IDOL-ARCHIVE/README.TXT','K-IDOL-ARCHIVE/extra','C:/outside','K-IDOL-ARCHIVE\\outside']:
        archive(path,extra)
        with pytest.raises(ValueError):verify(path)


def test_release_entry_smoke_uses_temporary_library(tmp_path):
    report=tmp_path/'smoke.json';user=tmp_path/'user-library';user.mkdir();sentinel=user/'sentinel';sentinel.write_bytes(b'unchanged')
    root=Path(__file__).resolve().parents[1];env=os.environ.copy();env.update(QT_QPA_PLATFORM='offscreen',K_IDOL_DATA_DIR=str(user))
    subprocess.run([sys.executable,str(root/'scripts/windows_entry.py'),'--smoke-test',str(report)],cwd=tmp_path,env=env,check=True,timeout=35)
    result=json.loads(report.read_text());assert result['success'] and all(result['checks'].values())
    assert list(user.iterdir())==[sentinel] and sentinel.read_bytes()==b'unchanged'
