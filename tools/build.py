from pathlib import Path
import hashlib, json, zipfile

root=Path(__file__).resolve().parents[1]
package=root/'source'/'color_prime'
files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.iterdir())
       if p.is_file() and p.suffix=='.py'}
(package/'package_integrity.json').write_text(json.dumps({'version':'3.5.2','files':files},indent=2)+'\n',encoding='utf8')
out=root/'dist';out.mkdir(exist_ok=True)
archive=out/'Color_Prime_Studio_3.5.2_Universal.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(package.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.blend1','.blend2'}:
            z.write(p,'color_prime/'+p.relative_to(package).as_posix())
print(archive.name)
print('SHA256',hashlib.sha256(archive.read_bytes()).hexdigest())

digest=hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix('.zip.sha256').write_text(digest+'  '+archive.name+'\n',encoding='ascii')
