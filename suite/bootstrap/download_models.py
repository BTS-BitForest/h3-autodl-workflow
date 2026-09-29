#!/usr/bin/env python3
"""Pinned, anonymous, direct downloads. Range resume + bounded source failover."""
import argparse,concurrent.futures,fcntl,hashlib,json,os,re,shutil,threading,time,urllib.parse,urllib.request
from pathlib import Path
CHUNK=32*1024**2
HOSTS={'modelscope.cn','hf-mirror.com','huggingface.co'}
def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
 return h.hexdigest()
def atomic(path,data):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2));os.replace(tmp,path)
def safe(root,rel):
 p=Path(rel)
 if p.is_absolute() or '..' in p.parts:raise ValueError('Unsafe manifest path')
 dest=(root/p).resolve()
 if not dest.is_relative_to(root.resolve()):raise ValueError('Manifest escapes destination')
 return dest
def validate(item):
 if not re.fullmatch('[0-9a-f]{64}',item['sha256']) or not isinstance(item['size'],int) or item['size']<=0:raise ValueError('Missing integrity metadata')
 for url in item['sources']:
  u=urllib.parse.urlsplit(url)
  if u.scheme!='https' or u.hostname not in HOSTS or u.username or u.password:raise ValueError('Unapproved source')
  if not re.search(r'/resolve/[0-9a-f]{40}/',u.path):raise ValueError('Source must pin full commit')
class Status:
 def __init__(self,path):self.path=path;self.lock=threading.Lock();self.state={};self.last=0
 def update(self,force=False,**changes):
  with self.lock:
   self.state.update(changes)
   if force or time.monotonic()-self.last>1:
    self.last=time.monotonic();atomic(self.path,dict(self.state,updated_at=time.time()))
class Downloader:
 def __init__(self,root,bundled,status,workers=4):self.root=root;self.bundled=bundled;self.status=status;self.workers=workers
 def open_range(self,url,start,end,total):
  # Range-specific query prevents intermediary caches serving the wrong segment.
  url+=('&' if '?' in url else '?')+f'download=true&h3_range={start}-{end}'
  req=urllib.request.Request(url,headers={'Range':f'bytes={start}-{end}','Accept-Encoding':'identity','User-Agent':'H3-Bootstrap/2'})
  r=urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=12)
  valid=(r.status==206 and r.headers.get('Content-Range')==f'bytes {start}-{end}/{total}')
  if not valid:r.close();raise IOError('Source does not honor the requested byte range')
  return r
 def rank(self,item):
  def probe(url):
   begin=time.monotonic();n=min(item['size'],1024**2)
   try:
    with self.open_range(url,0,n-1,item['size']) as r:
     got=0
     while got<n:
      if time.monotonic()-begin>20:raise TimeoutError('Source probe budget exceeded')
      b=r.read1(min(128*1024,n-got))
      if not b:raise IOError('Short probe')
      got+=len(b)
    return {'url':url,'speed':n/max(.001,time.monotonic()-begin),'ok':True}
   except Exception as e:return {'url':url,'speed':0,'ok':False,'error':type(e).__name__}
  with concurrent.futures.ThreadPoolExecutor(max_workers=min(3,len(item['sources']))) as pool:results=list(pool.map(probe,item['sources']))
  # Preserve the measured release preference among reachable sources.
  # A tiny availability probe is not a sustained bandwidth benchmark.
  results.sort(key=lambda x:(not x['ok'],item['sources'].index(x['url'])))
  self.status.update(force=True,source_tests=[{'host':urllib.parse.urlsplit(x['url']).hostname,'MiB_s':round(x['speed']/2**20,3),'ok':x['ok']} for x in results])
  if not any(x['ok'] for x in results):raise IOError('All model sources failed their range probe; retry later, existing data preserved')
  return [x['url'] for x in results]
 def download(self,item):
  validate(item);dest=safe(self.root,item['destination']);dest.parent.mkdir(parents=True,exist_ok=True)
  self.status.update(force=True,phase='checking',file=item['destination'],bytes=0,size=item['size'],source_tests=[],source=None)
  if dest.exists():
   if dest.stat().st_size==item['size'] and sha(dest)==item['sha256']:return 'verified-existing'
   raise IOError('Existing final file differs from the manifest; preserved for inspection')
  if item.get('bundled'):
   src=safe(self.bundled,item['bundled'])
   if src.stat().st_size!=item['size'] or sha(src)!=item['sha256']:raise IOError('Bundled file integrity failure')
   if shutil.disk_usage(dest.parent).free<item['size']+1024**3:raise IOError('Insufficient destination disk space')
   tmp=dest.with_name(dest.name+'.copying');shutil.copyfile(src,tmp);os.replace(tmp,dest);return 'bundled'
  if not item['sources']:raise IOError('No sources configured')
  sources=self.rank(item);tmp=dest.with_name(dest.name+'.incomplete');meta=tmp.with_name(tmp.name+'.json')
  state={'sha256':item['sha256'],'size':item['size'],'chunk':CHUNK,'done':{}}
  if meta.exists():
   prior=json.loads(meta.read_text())
   if any(prior.get(k)!=state[k] for k in ('sha256','size','chunk')):raise IOError('Resume metadata belongs to another model version')
   state=prior
  if state['done'] and not tmp.exists():raise IOError('Resume file missing; metadata preserved')
  fd=os.open(tmp,os.O_RDWR|os.O_CREAT,0o600)
  try:
   if tmp.stat().st_size not in (0,item['size']):raise IOError('Invalid partial file size')
   os.ftruncate(fd,item['size'])
   # Re-check completed chunks after restart; incomplete writes cannot become trusted data.
   for key,value in list(state['done'].items()):
    start=int(key);length=min(CHUNK,item['size']-start)
    if start<0 or start%CHUNK or length<=0:raise IOError('Invalid resume offset')
    if hashlib.sha256(os.pread(fd,length,start)).hexdigest()!=value:del state['done'][key]
   completed=sum(min(CHUNK,item['size']-int(k)) for k in state['done'])
   if shutil.disk_usage(dest.parent).free<item['size']-completed+1024**3:raise IOError('Insufficient disk space with 1GiB reserve')
   atomic(meta,state);lock=threading.Lock();inflight={};abort=threading.Event();net_samples=[]
   def part(start):
    nonlocal completed
    if str(start) in state['done']:return
    end=min(item['size'],start+CHUNK)-1;length=end-start+1
    for attempt in range(4):
     for url in sources:
      if abort.is_set():raise IOError('Download stopped after another chunk failed')
      received=0;begin=time.monotonic();h=hashlib.sha256()
      try:
       with self.open_range(url,start,end,item['size']) as r:
        while received<length:
         if abort.is_set():raise IOError('Cancelled')
         elapsed=time.monotonic()-begin
         if elapsed>600 or (elapsed>60 and received/max(elapsed,1)<32*1024):raise TimeoutError('Slow source, trying another')
         b=r.read1(min(256*1024,length-received))
         if not b:raise IOError('Unexpected end of range')
         written=0
         while written<len(b):
          n=os.pwrite(fd,b[written:],start+received+written)
          if n<=0:raise IOError('Write failed')
          written+=n
         h.update(b);received+=len(b)
         with lock:
          inflight[start]=received
          now=time.monotonic();net_samples.append((now,len(b)))
          while net_samples and now-net_samples[0][0]>10:net_samples.pop(0)
          span=now-net_samples[0][0] if net_samples else 0
          speed=round(sum(x[1] for x in net_samples)/span/1e6,2) if span>=2 else None
          self.status.update(phase='downloading',bytes=completed+sum(inflight.values()),source=urllib.parse.urlsplit(url).hostname,download_MB_s=speed)
       os.fsync(fd)
       with lock:
        state['done'][str(start)]=h.hexdigest();completed+=length;inflight.pop(start,None);atomic(meta,state)
        self.status.update(force=True,phase='downloading',bytes=completed+sum(inflight.values()))
       return
      except Exception as exc:
       print(json.dumps({"retry_chunk":start,"attempt":attempt+1,"host":urllib.parse.urlsplit(url).hostname,"reason":type(exc).__name__}),flush=True)
       with lock:inflight.pop(start,None)
     time.sleep(min(10,2**attempt))
    abort.set();raise IOError('Chunk exhausted sources; verified progress retained')
   offsets=[n for n in range(0,item['size'],CHUNK) if str(n) not in state['done']]
   with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as pool:list(pool.map(part,offsets))
  finally:os.close(fd)
  self.status.update(force=True,phase='verifying',bytes=item['size'])
  if sha(tmp)!=item['sha256']:
   # Discard chunk trust so a later retry fetches again; never promote a wrong model.
   state['done']={};atomic(meta,state);raise IOError('Final SHA256 mismatch; model not activated')
  os.replace(tmp,dest);meta.unlink(missing_ok=True);return 'downloaded'
def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,default=Path(__file__).with_name('models.json'));p.add_argument('--root',type=Path,default=Path('/root/autodl-tmp/h3/models'));p.add_argument('--only',default='');p.add_argument('--workers',type=int,default=4);p.add_argument('--allow-system-disk',action='store_true');a=p.parse_args()
 a.root.mkdir(parents=True,exist_ok=True)
 if not a.allow_system_disk and a.root.stat().st_dev==Path('/').stat().st_dev:raise SystemExit('Models must be on the data disk')
 status=Status(a.root.parent/'bootstrap/status.json');lockfile=a.root.parent/'bootstrap/download.lock';lockfile.parent.mkdir(exist_ok=True)
 with lockfile.open('w') as f:
  try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise SystemExit('Another downloader is already running')
  manifest=json.loads(a.manifest.read_text());items=[x for x in manifest['files'] if not a.only or x['destination']==a.only]
  if not items:raise SystemExit('No matching model')
  d=Downloader(a.root,a.manifest.parent/'bundled',status,max(1,min(8,a.workers)))
  status.update(force=True,state='running',file_count=len(items),total_size=sum(x['size'] for x in items),completed_files=0,error=None)
  try:
   for i,item in enumerate(items):
    result=d.download(item);status.update(force=True,completed_files=i+1);print(json.dumps({'file':item['destination'],'result':result},ensure_ascii=False),flush=True)
  except Exception as e:
   status.update(force=True,state='failed',error=str(e));raise
  status.update(force=True,state='complete',phase='ready')
if __name__=='__main__':main()
