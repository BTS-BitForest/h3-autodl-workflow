#!/usr/bin/env python3
"""Spawn a resumable download supervisor. Never restart an active download."""
import fcntl,json,os,subprocess,sys,time
from pathlib import Path
SUITE=Path(__file__).resolve().parents[1]
ROOT=Path(os.environ.get('H3_ROOT','/root/autodl-tmp/h3'))
STATE=ROOT/'bootstrap'
def main():
 STATE.mkdir(parents=True,exist_ok=True)
 if (STATE/'migration.inprogress').exists():return
 with (STATE/'supervisor.lock').open('a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:return
  if '--worker' not in sys.argv:
   try:
    info=json.loads((STATE/'status.json').read_text())
    if info.get('state')=='complete' and all((ROOT/'models'/x['destination']).is_file() and (ROOT/'models'/x['destination']).stat().st_size==x['size'] for x in json.loads((SUITE/'bootstrap/models.json').read_text())['files']):return
   except (OSError,ValueError):pass
   # Transfer the supervisor lock to the child, avoiding the spawn race.
   with (STATE/'download.log').open('ab') as log:
    env={k:v for k,v in os.environ.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy')}
    env['H3_DOWNLOAD_LOCK_FD']=str(lock.fileno())
    proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker'],env=env,pass_fds=(lock.fileno(),),stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    (STATE/'download.pid').write_text(str(proc.pid))
   return

def worker():
 # Keep the inherited descriptor alive for the supervisor's lifetime.
 fd=int(os.environ.pop('H3_DOWNLOAD_LOCK_FD'))
 for attempt in range(6):
  result=subprocess.run([sys.executable,str(SUITE/'bootstrap/download_models.py'),'--root',str(ROOT/'models'),'--workers','4'])
  if result.returncode==0:return
  if attempt<5:time.sleep(min(120,15*(attempt+1)))
 print('Automatic retries exhausted. Use the panel Retry download button.',flush=True)
if __name__=='__main__':
 if '--worker' in sys.argv:worker()
 else:main()
