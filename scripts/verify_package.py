"""Verify a release archive before extraction; does not execute its contents."""
import hashlib,json,sys,zipfile
from pathlib import PurePosixPath,Path


def verify(path):
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist();seen=set()
        for name in names:
            parts=PurePosixPath(name).parts
            if '\\' in name or not parts or parts[0]!='K-IDOL-ARCHIVE' or '..' in parts or ':' in name or name.casefold() in seen:raise ValueError('Unsafe or duplicate archive path')
            seen.add(name.casefold())
            if archive.getinfo(name).external_attr>>16 & 0o170000==0o120000:raise ValueError('Archive symlink is not allowed')
        manifest_name='K-IDOL-ARCHIVE/RELEASE-MANIFEST.json'
        manifest=json.loads(archive.read(manifest_name))
        expected={manifest_name}|{'K-IDOL-ARCHIVE/'+name for name in manifest['files']}
        if set(names)!=expected:raise ValueError('Archive inventory mismatch')
        for name,record in manifest['files'].items():
            data=archive.read('K-IDOL-ARCHIVE/'+name)
            if len(data)!=record['bytes'] or hashlib.sha256(data).hexdigest()!=record['sha256']:raise ValueError('Archive checksum mismatch: '+name)
        if archive.testzip() is not None:raise ValueError('Archive CRC failure')
        return dict(version=manifest['version'],kind=manifest['kind'],files=len(manifest['files']),success=True)

if __name__=='__main__':print(json.dumps(verify(Path(sys.argv[1])),indent=2))
