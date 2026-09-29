#!/usr/bin/env python3
"""Download GitHub assets with visible progress; verify before extraction."""
import argparse,hashlib,json,subprocess,time
from pathlib import Path

def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
 return h.hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--repo',required=True);ap.add_argument('--tag',required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
 manifest=json.loads(Path(__file__).with_name('assets.json').read_text())
 a.output.mkdir(parents=True,exist_ok=True)
 for i,x in enumerate(manifest['files'],1):
  name=x['name']
  if Path(name).name!=name:raise SystemExit('Invalid asset path')
  p=a.output/name
  if p.is_file() and p.stat().st_size==x['size'] and digest(p)==x['sha256']:
   print(f'[{i}/{len(manifest["files"])}] {name}: 已校验，跳过',flush=True);continue
  if p.exists() and p.stat().st_size>=x['size']:p.unlink()
  for attempt in range(3):
   url=f'https://github.com/{a.repo}/releases/download/{a.tag}/{name}'
   proc=subprocess.Popen(['curl','--fail','--location','--silent','--show-error','--connect-timeout','30','--speed-limit','1024','--speed-time','60','--continue-at','-','--output',str(p),url])
   try:
    while proc.poll() is None:
     n=p.stat().st_size if p.exists() else 0
     print(f'[{i}/{len(manifest["files"])}] {name}: {n/2**20:.1f}/{x["size"]/2**20:.1f} MiB ({n/x["size"]:.1%})',flush=True);time.sleep(3)
   except BaseException:
    proc.terminate();proc.wait();raise
   if proc.returncode==0 and p.is_file() and p.stat().st_size==x['size'] and digest(p)==x['sha256']:break
   if p.exists() and (p.stat().st_size>=x['size'] or proc.returncode==33):p.unlink()
   print('下载或校验失败，重试 '+str(attempt+1),flush=True)
  else:raise SystemExit('无法下载完整文件：'+name)
 print('运行环境下载及 SHA256 校验完成。',flush=True)
if __name__=='__main__':main()
