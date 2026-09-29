#!/usr/bin/env python3
import json,time,argparse
from pathlib import Path

def describe(s):
 n=s.get('bytes',0);total=s.get('size',0)
 return f"模型 {s.get('completed_files',0)}/{s.get('file_count','?')} | {s.get('phase','等待')} | {s.get('file','')} | {n/2**30:.2f}/{total/2**30:.2f} GiB | {s.get('download_MB_s') or 0} MB/s"
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--status',type=Path,default=Path('/root/autodl-tmp/h3/bootstrap/status.json'));a=ap.parse_args()
 start=time.monotonic()
 while True:
  try:s=json.loads(a.status.read_text())
  except (OSError,ValueError):
   if time.monotonic()-start>120:raise SystemExit('下载状态未出现，请检查 bootstrap/download.log')
   time.sleep(3);continue
  print(describe(s),flush=True)
  if s.get('state')=='complete':print('全部模型就绪，可以提交任务。',flush=True);return
  if s.get('state')=='failed':
   print('本轮下载失败，后台有有限重试；可在面板查看并点击重试。错误：'+str(s.get('error')),flush=True);return
  time.sleep(3)
if __name__=='__main__':main()
