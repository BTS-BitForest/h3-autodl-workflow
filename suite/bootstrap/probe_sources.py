import concurrent.futures,json,time,urllib.request,urllib.parse,sys
from pathlib import Path
m=json.loads(Path(sys.argv[1]).read_text())['files']
chosen=[next(x for x in m if x['destination'].startswith(p) and x.get('sources')) for p in ['diffusion_models/','loras/','pe/']]
def probe(args):
 item,url=args;start=time.monotonic();n=min(item['size'],4*1024**2);end=n-1;row={'file':item['destination'],'source':urllib.parse.urlsplit(url).netloc}
 try:
  req=urllib.request.Request(url+'?download=true&h3_probe=0',headers={'Range':f'bytes=0-{end}','Accept-Encoding':'identity'})
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=12) as r:
   if r.status!=206 or r.headers.get('Content-Range')!=f'bytes 0-{end}/{item["size"]}':raise ValueError('Range response mismatch')
   received=0
   while received<n:
    if time.monotonic()-start>30:raise TimeoutError('sample time budget')
    b=r.read1(min(256*1024,n-received))
    if not b:raise IOError('short response')
    received+=len(b)
  elapsed=time.monotonic()-start;row.update(ok=True,seconds=round(elapsed,2),MiB_s=round(n/2**20/elapsed,3))
 except Exception as e:row.update(ok=False,error=type(e).__name__,seconds=round(time.monotonic()-start,2))
 return row
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 rows=list(pool.map(probe,[(item,url) for item in chosen for url in item['sources']]))
print(json.dumps(rows,ensure_ascii=False,indent=2))
