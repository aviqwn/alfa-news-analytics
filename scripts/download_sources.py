"""Download the organizer snapshot using source IDs and SHA256 from the committed manifest."""
import hashlib,json,subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[1]
manifest=ROOT/'reports/data_manifest.json'
files=(json.loads(manifest.read_text())['files'] if manifest.exists() else json.loads((ROOT/'data/raw/source-pages/files.json').read_text()))
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as stream:
  for data in iter(lambda:stream.read(1024*1024),b''):h.update(data)
 return h.hexdigest()
def download(f):
 p=ROOT/'data/raw'/f['kind']/f['name'];p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists() and p.stat().st_size>10000:
  if f.get('sha256') and digest(p)!=f['sha256']:raise RuntimeError('Existing source checksum mismatch: '+f['name'])
  return
 partial=p.with_suffix(p.suffix+'.part')
 r=subprocess.run(['curl','-sS','-L','--fail','--retry','2','--max-time','120','https://drive.usercontent.google.com/download?id='+f['id']+'&export=download&confirm=t','-o',str(partial)],capture_output=True,text=True)
 if r.returncode:raise RuntimeError(r.stderr)
 with partial.open('rb') as stream:header=stream.read(100)
 if f['kind']=='analytics' and not header.startswith(b'%PDF'):raise RuntimeError('Download is not PDF: '+f['name'])
 if f['kind']=='companies' and b';' not in header:raise RuntimeError('Download is not semicolon CSV: '+f['name'])
 if f.get('sha256') and digest(partial)!=f['sha256']:raise RuntimeError('Source has changed; investigate before updating manifest: '+f['name'])
 partial.rename(p);print('Downloaded',f['name'],p.stat().st_size,flush=True)
with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(download,files))
registry=ROOT/'data/raw/source-pages/files.json';registry.parent.mkdir(parents=True,exist_ok=True)
registry.write_text(json.dumps([{k:f[k] for k in ['id','name','kind','url']} for f in files],ensure_ascii=False,indent=2)+'\n')
