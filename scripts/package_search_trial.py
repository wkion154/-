"""Package the existing application with its experimental Windows browser bridge."""
import hashlib,json,sys,zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from verify_package import verify
excluded={'.git','.venv','dist','build','releases','__pycache__','.pytest_cache'}
files=sorted(p for p in root.rglob('*') if p.is_file() and not excluded.intersection(p.relative_to(root).parts))
target=root/'releases/K-IDOL-ARCHIVE_Search_Stage1_Experimental.zip';target.parent.mkdir(exist_ok=True)
manifest=dict(version='0.7.4-search-stage1-experimental',kind='windows-source-experimental',files={})
with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
    for path in files:
        if path.is_symlink():raise ValueError('Symlinks are not release files')
        name=path.relative_to(root).as_posix();data=path.read_bytes()
        if path.suffix=='.bat':data=data.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
        manifest['files'][name]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest());z.writestr('K-IDOL-ARCHIVE/'+name,data)
    z.writestr('K-IDOL-ARCHIVE/RELEASE-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
verify(target)
target.with_suffix('.zip.sha256').write_text(hashlib.sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n')
print(target)
