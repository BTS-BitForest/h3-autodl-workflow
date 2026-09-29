#!/usr/bin/env python3
"""Install a verified release on a fresh Ubuntu22.04 AutoDL instance."""
import argparse,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path

def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(4*1024**2),b''):h.update(block)
 return h.hexdigest()
def extract(parts,dest):
 dest.mkdir(parents=True,exist_ok=True)
 reader=subprocess.Popen(['cat',*[str(p) for p in parts]],stdout=subprocess.PIPE)
 decoder=subprocess.Popen(['zstd','-d','-c'],stdin=reader.stdout,stdout=subprocess.PIPE);reader.stdout.close()
 try:
  subprocess.run(['tar','--no-same-owner','-xf','-','-C',str(dest)],stdin=decoder.stdout,check=True)
 finally:decoder.stdout.close()
 if decoder.wait() or reader.wait():raise RuntimeError('Runtime extraction failed')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--assets',type=Path,required=True,help='Directory containing every GitHub Release asset');ap.add_argument('--check-only',action='store_true');args=ap.parse_args()
 repo=Path(__file__).resolve().parents[1];manifest=json.loads((repo/'install/assets.json').read_text())
 for item in manifest['files']:
  if Path(item['name']).name!=item['name']:raise ValueError('Invalid asset path')
  p=args.assets/item['name']
  if not p.is_file() or p.stat().st_size!=item['size'] or digest(p)!=item['sha256']:raise SystemExit('Missing or corrupt release asset: '+item['name'])
 print('All release assets verified.')
 if args.check_only:return
 if os.geteuid()!=0:raise SystemExit('Run as root on a fresh instance')
 suite=Path('/opt/h3-suite');python=Path('/opt/h3-python')
 if suite.exists() or python.exists():raise SystemExit('Existing H3 installation detected; refusing to overwrite it')
 if shutil.disk_usage('/opt').free<16*1024**3:raise SystemExit('At least16GiB free system disk space required')
 data=Path('/root/autodl-tmp');data.mkdir(exist_ok=True)
 if data.stat().st_dev==Path('/').stat().st_dev:raise SystemExit('Separate AutoDL data disk required')
 env={k:v for k,v in os.environ.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy')};env['DEBIAN_FRONTEND']='noninteractive'
 subprocess.run(['apt-get','update','-qq'],env=env,check=True)
 subprocess.run(['apt-get','install','-y','ffmpeg','libgl1','libglib2.0-0','zstd'],env=env,check=True)
 parts=sorted(args.assets.glob('H3-Python.tar.zst.part*'))
 if [p.name for p in parts]!=manifest['python_parts']:raise SystemExit('Unexpected Python archive part set')
 extract(parts,python)
 shutil.copytree(repo/'suite',suite,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 extract([args.assets/'H3-Venv.tar.zst'],suite/'venv')
 subprocess.run([str(python/'bin/python'),'-m','venv','--system-site-packages',str(suite/'venv')],check=True)
 extract([args.assets/'H3-Agents.tar.zst'],suite)
 Path('/工具包').mkdir(exist_ok=True);shutil.copy2(args.assets/'H3-Windows-Launcher.zip','/工具包/H3控制端.zip')
 subprocess.run(['bash',str(suite/'scripts/install_entries.sh')],check=True)
 subprocess.run([str(suite/'venv/bin/python'),'-c','import torch,transformers,av,sageattention; print(torch.__version__,torch.version.cuda); assert torch.cuda.is_available(), "GPU/driver not ready"'],check=True)
 print('Installation complete. Run /opt/h3-suite/venv/bin/python /opt/h3-suite/scripts/launch_session.py or connect using the Windows launcher.')
if __name__=='__main__':main()
