#!/usr/bin/env python3
import fcntl,os,socket,subprocess,time,urllib.request,json
from pathlib import Path
source=Path(__file__).resolve().parent
root=Path('/root/autodl-tmp/h3/network');root.mkdir(parents=True,exist_ok=True)
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def ready():
 try:
  with op.open('http://127.0.0.1:41080/health',timeout=.5) as r:return json.load(r).get('service')=='h3-network-router'
 except Exception:return False
if not ready():
 with (root/'start.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if not ready():
   with (root/'router.log').open('ab') as log:
    env={k:v for k,v in os.environ.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy')}
    subprocess.Popen(['/opt/h3-python/bin/python',str(source/'router.py')],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
   for _ in range(10):
    if ready():break
    time.sleep(.1)
   else:raise SystemExit('Network router failed; inspect '+str(root/'router.log'))
