"""Preserve installed dependency license files in the Windows distribution."""
import importlib.metadata
from pathlib import Path
import shutil,sys
output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=True)
packages=['PySide6','PySide6-Essentials','PySide6-Addons','shiboken6','Pillow']
manifest=[]
for name in packages:
    dist=importlib.metadata.distribution(name)
    manifest.append(f'{name} {dist.version}\n{dist.metadata.get("License", "See bundled notices and upstream license files")}')
    for file in dist.files or []:
        if any(token in str(file).lower() for token in ('license','copying','copyright')):
            source=Path(dist.locate_file(file))
            if source.is_file():
                destination=output/name/str(file)
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,destination)
(output/'DEPENDENCIES.txt').write_text('\n\n'.join(manifest),encoding='utf-8')
