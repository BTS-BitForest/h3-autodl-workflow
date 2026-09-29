#!/usr/bin/env python3
"""Loopback-only HTTPS tunnel router; TLS stays end-to-end, no API keys inspected."""
import asyncio,contextlib,json,os,ssl,time,urllib.parse
HOST='127.0.0.1';PORT=int(os.environ.get('H3_ROUTER_PORT','41080'))
UPSTREAMS=[int(x) for x in os.environ.get('H3_ROUTER_UPSTREAMS','41084').split(',') if x]
CACHE={};LAST={};TLS=ssl.create_default_context();LIMIT=asyncio.Semaphore(32)
def routes(host):
 direct=host=='api.deepseek.com' or host.endswith('.deepseek.com') or host in ('localhost','127.0.0.1','::1')
 options=['direct']+[str(p) for p in UPSTREAMS] if direct else [str(p) for p in UPSTREAMS]+['direct']
 return options
async def close(w):
 w.close()
 with contextlib.suppress(Exception):await asyncio.wait_for(w.wait_closed(),1)
async def dial(host,port,route):
 r,w=await asyncio.wait_for(asyncio.open_connection(host if route=='direct' else HOST,port if route=='direct' else int(route)),4)
 try:
  if route!='direct':
   w.write(f'CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n'.encode());await w.drain()
   h=await asyncio.wait_for(r.readuntil(b'\r\n\r\n'),5)
   if h.split(b' ')[1]!=b'200':raise OSError('upstream refused CONNECT')
  return r,w
 except BaseException:await close(w);raise
async def probe(host,port,route):
 r,w=await dial(host,port,route)
 try:
  if port==443:await asyncio.wait_for(w.start_tls(TLS,server_hostname=host),4)
 finally:await close(w)
async def select(host,port):
 key=(host,port);cached=CACHE.get(key);options=routes(host)
 if cached and time.monotonic()-cached[1]<15:options=[cached[0]]+[p for p in options if p!=cached[0]]
 for route in options:
  try:
   if not cached or cached[0]!=route or time.monotonic()-cached[1]>=15:await probe(host,port,route)
   pair=await dial(host,port,route);CACHE[key]=(route,time.monotonic());LAST[host]={'route':route,'at':int(time.time()),'reachable':True};return pair
  except (OSError,asyncio.TimeoutError,asyncio.IncompleteReadError,ValueError):CACHE.pop(key,None)
 LAST[host]={'route':None,'at':int(time.time()),'reachable':False};raise OSError('No reachable route. Check Windows network helper / VPN / SSH reverse forward.')
async def pump(r,w):
 while True:
  data=await asyncio.wait_for(r.read(65536),300)
  if not data:return
  w.write(data);await asyncio.wait_for(w.drain(),30)
async def handle(r,w):
 remote=None;connected=False
 try:
  async with LIMIT:
   head=await asyncio.wait_for(r.readuntil(b'\r\n\r\n'),10)
   if len(head)>16384:raise ValueError('Header too large')
   method,target,version=head.split(b'\r\n',1)[0].decode('ascii').split(' ')
   if method=='GET' and target=='/health':
    body=json.dumps({'service':'h3-network-router','port':PORT,'upstreams':UPSTREAMS,'routes':LAST}).encode();w.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body);await w.drain();return
   if method=='CONNECT':
    h,sep,p=target.rpartition(':');host=h.strip('[]');port=int(p)
    if not host or not sep or not 1<=port<=65535 or any(c in host for c in '\r\n /@'):raise ValueError('Bad target')
   else:
    u=urllib.parse.urlsplit(target)
    if u.scheme!='http' or u.username:raise ValueError('Only HTTP absolute URLs and CONNECT supported')
    host=u.hostname;port=u.port or 80
   rr,remote=await asyncio.wait_for(select(host,port),25)
   if method=='CONNECT':w.write(b'HTTP/1.1 200 Connection Established\r\n\r\n');await w.drain();connected=True
   else:
    lines=head.split(b'\r\n')[1:];lines=[l for l in lines if l and l.split(b':',1)[0].lower() not in (b'proxy-authorization',b'proxy-connection',b'connection')]
    path=u.path or '/'
    if u.query:path+='?'+u.query
    remote.write(f'{method} {path} {version}\r\n'.encode()+b'\r\n'.join(lines)+b'\r\nConnection: close\r\n\r\n');await remote.drain();connected=True
  tasks=[asyncio.create_task(pump(r,remote)),asyncio.create_task(pump(rr,w))]
  try:await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
  finally:
   for t in tasks:t.cancel()
   await asyncio.gather(*tasks,return_exceptions=True)
 except Exception:
  if not connected:
   with contextlib.suppress(Exception):
    body=b'No reachable network route. Check Windows helper, SSH reverse forwarding and VPN. Local H3 generation is unaffected.\n';w.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body);await w.drain()
 finally:
  if remote:await close(remote)
  await close(w)
async def main():
 server=await asyncio.start_server(handle,HOST,PORT,limit=16384)
 async with server:await server.serve_forever()
if __name__=='__main__':asyncio.run(main())
