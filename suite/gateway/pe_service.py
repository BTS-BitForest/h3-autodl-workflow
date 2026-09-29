"""Durable local prompt rewriting. Child lifetime is bounded by a GPU mutex."""
import asyncio,contextlib,fcntl,hashlib,json,os,signal,time,uuid
from pathlib import Path
from aiohttp import web, ClientConnectionError
from pe_continuation import Continuations

TERMINAL=('succeeded','failed','interrupted')
class PE:
 def __init__(self,gateway,root,state):
  self.g=gateway;self.root=root;self.state=state;self.process=None
  self.execution=None;self.executing_id=None
  self.db=gateway.db
  self.continuations=Continuations(gateway)
  self.db.execute('CREATE TABLE IF NOT EXISTS pe_jobs(id TEXT PRIMARY KEY,request TEXT,state TEXT,created REAL,updated REAL,result TEXT,error TEXT)')
  self.db.execute("UPDATE pe_jobs SET state='interrupted',error='服务重启，未自动重复改写；请重新发起。' WHERE state='running'")
  self.db.commit()
 def first(self):return self.db.execute("SELECT * FROM pe_jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
 def precedes(self,video):
  row=self.first()
  return bool(row and (video is None or (video['state']=='queued' and row['created']<video['created'])))
 def update(self,jid,state,result=None,error=''):
  self.db.execute('UPDATE pe_jobs SET state=?,updated=?,result=?,error=? WHERE id=?',(state,time.time(),json.dumps(result,ensure_ascii=False) if result else None,error,jid));self.db.commit()
 def public(self,row):
  return {'id':row['id'],'status':row['state'],'created':row['created'],'updated':row['updated'],'result':json.loads(row['result']) if row['result'] else None,'error':row['error'],'continuation':self.continuations.status(row['id'])}
 async def status(self,req):
  row=self.db.execute('SELECT * FROM pe_jobs WHERE id=?',(req.match_info['pid'],)).fetchone()
  if not row:raise web.HTTPNotFound()
  return web.json_response(self.public(row))
 async def info(self,req):
  model=self.root/'models/pe/Qwen3.5-4B'
  ready=(model/'model.safetensors.index.json').is_file()
  if ready:
   try:ready=all((model/x).is_file() for x in set(json.loads((model/'model.safetensors.index.json').read_text())['weight_map'].values()))
   except Exception:ready=False
  rows=self.db.execute("SELECT * FROM pe_jobs WHERE state IN ('queued','running') ORDER BY created").fetchall()
  return web.json_response({'model':'Qwen3.5-4B','ready':ready,'local_only':True,'queue':[self.public(x) for x in rows],'recent':[self.public(x) for x in self.db.execute('SELECT * FROM pe_jobs ORDER BY created DESC LIMIT 10').fetchall()],'gpu_policy':'exclusive-process-lock','video_audio_analysis':False})
 async def submit(self,req):
  data=await req.json()
  if not isinstance(data,dict):raise ValueError('Expected object')
  if set(data)-{'request_id','prompt','images','videos','audios','bindings','frames','width','height','workflow','sound_policy','dialogue_text','dialogue_speaker','dialogue_contract','control_level','language','video_request'}:raise ValueError('Unknown PE fields')
  jid=str(uuid.UUID(data['request_id']));text=data.get('prompt','')
  if not isinstance(text,str) or not 1<=len(text.strip())<=12000:raise ValueError('提示词长度须为1–12000字符')
  if data.get('language','zh') not in ('zh','en'):raise ValueError('Unknown language')
  if data.get('sound_policy','no_voice') not in ('no_voice','specified_dialogue'):raise ValueError('Unknown sound policy')
  if data.get('sound_policy')=='specified_dialogue' and not data.get('dialogue_text','').strip():raise ValueError('请填写需要保留的中文台词')
  if type(data.get('frames',124)) is not int or not 5<=data.get('frames',124)<=360:raise ValueError('PE镜头时长须在15秒以内')
  total=0
  for key,kind,max_count in [('images','image',9),('videos','video',3),('audios','audio',3)]:
   ids=data.get(key,[])
   if not isinstance(ids,list) or len(ids)>max_count:raise ValueError('参考素材数量超限')
   total+=len(ids)
   for aid in ids:
    if not isinstance(aid,str):raise ValueError('素材编号无效')
    row=self.db.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
    if not row or row['kind']!=kind:raise ValueError('参考素材不存在或类型不符')
  if total>12:raise ValueError('最多12个参考文件')
  if data.get('sound_policy')=='specified_dialogue':
   c=data.get('dialogue_contract')
   if not isinstance(c,dict):raise ValueError('指定对白PE需要完整dialogue_contract，不能绕过现有音频校验')
   if data.get('control_level')!='best_effort':raise ValueError('请明确选择best_effort，模型不保证逐字声音和口型')
   if data.get('videos'):raise ValueError('指定对白不接受视频参考，避免第二声源')
   if c.get('audio_mode','reuse')=='native':
    if data.get('audios') or c.get('native_dialogue_approved') is not True or not c.get('approval_reference') or c.get('voice_type')!='onscreen_dialogue':raise ValueError('原生对白须显式确认、画内说话且无音视频参考')
   else:
    ids=data.get('audios',[]);approved=c.get('approved_audio',{})
    if len(ids)!=1 or approved.get('asset_id')!=ids[0] or approved.get('approved') is not True or not approved.get('approval_reference'):raise ValueError('须提供唯一获批参考音频')
    asset=self.db.execute('SELECT sha256 FROM assets WHERE id=?',(ids[0],)).fetchone()
    if not asset or approved.get('sha256')!=asset['sha256']:raise ValueError('参考音频SHA256不匹配')
  video=data.get('video_request')
  if video is not None:
   if not isinstance(video,dict):raise ValueError('video_request须为对象')
   if str(uuid.UUID(video.get('request_id','')))!=jid:raise ValueError('视频与PE任务编号必须相同')
  encoded=json.dumps(data,sort_keys=True,ensure_ascii=False)
  row=self.db.execute('SELECT * FROM pe_jobs WHERE id=?',(jid,)).fetchone()
  if row:
   if row['request']!=encoded:raise web.HTTPConflict(text='request_id already used')
   return web.json_response(self.public(row))
  if video is not None and self.db.execute('SELECT id FROM jobs WHERE id=?',(jid,)).fetchone():raise web.HTTPConflict(text='视频任务编号已存在')
  if self.db.execute("SELECT count(*) FROM pe_jobs WHERE state IN ('queued','running')").fetchone()[0]>=20:raise web.HTTPTooManyRequests()
  now=time.time();self.db.execute('INSERT INTO pe_jobs VALUES(?,?,?,?,?,?,?)',(jid,encoded,'queued',now,now,None,''));
  if video is not None:self.continuations.add(jid,video)
  self.db.commit()
  return web.json_response({'id':jid,'status':'queued'},status=202)
 async def run(self):
  while True:
   row=None
   try:
    await self.continuations.run_once()
    row=self.first();video=self.db.execute("SELECT * FROM jobs WHERE state NOT IN ('succeeded','failed','interrupted') ORDER BY created LIMIT 1").fetchone()
    if row and self.precedes(video):
     q=await self.g.comfy('/queue')
     if not q['queue_running'] and not q['queue_pending']:
      current=self.db.execute('SELECT state FROM pe_jobs WHERE id=?',(row['id'],)).fetchone()
      if current['state']!='queued':continue
      self.executing_id=row['id'];self.execution=asyncio.create_task(self.execute(dict(row)))
      try:await self.execution
      except asyncio.CancelledError:
       if asyncio.current_task().cancelling():raise
      finally:self.execution=None;self.executing_id=None
   except asyncio.CancelledError:raise
   except (ClientConnectionError,asyncio.TimeoutError):
    # A temporarily unavailable backend must not destroy a queued rewrite.
    pass
   except Exception as exc:
    if row:self.update(row['id'],'failed',error=str(exc)[:1000])
   await asyncio.sleep(1)
 async def execute(self,row):
  import sys
  lock=(self.state/'gpu.lock').open('a')
  try:
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:return
   # The ComfyUI execution wrapper uses this exact file, including non-gateway submissions.
   headers={'Authorization':'Bearer '+self.g.token}
   async with self.g.session.get(self.g.backend_url+'/h3-pe/gate',headers=headers) as r:
    if r.status!=200:raise RuntimeError('ComfyUI 显存互斥节点未就绪，拒绝加载PE')
    gate=await r.json()
    if gate.get('lock')!=str(self.state/'gpu.lock'):raise RuntimeError('PE与ComfyUI锁路径不一致')
   async with self.g.session.post(self.g.backend_url+'/h3-pe/unload',headers=headers,json={}) as r:
    if r.status!=200:raise RuntimeError('ComfyUI 尚未释放显存，拒绝加载PE')
   self.update(row['id'],'running')
   data=json.loads(row['request']);data.pop('video_request',None);data['image_paths']=[]
   for aid in data.get('images',[]):
    asset=self.db.execute('SELECT path FROM assets WHERE id=?',(aid,)).fetchone();p=(self.root/'input'/asset['path']).resolve()
    if not p.is_relative_to((self.root/'input').resolve()):raise ValueError('Invalid image path')
    data['image_paths'].append(str(p))
   work=self.state/'pe';work.mkdir(exist_ok=True)
   inp=work/(row['id']+'.input.json');out=work/(row['id']+'.output.json');log=work/(row['id']+'.log')
   inp.write_text(json.dumps(data,ensure_ascii=False));os.chmod(inp,0o600)
   env={k:v for k,v in os.environ.items() if k.upper() not in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY')}
   env.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',OMP_NUM_THREADS='8')
   with log.open('wb') as stream:
    spawn=asyncio.create_task(asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).with_name('pe_worker.py')),'--model',str(self.root/'models/pe/Qwen3.5-4B'),'--input',str(inp),'--output',str(out),env=env,stdout=stream,stderr=stream,pass_fds=(lock.fileno(),),start_new_session=True))
    try:self.process=await asyncio.shield(spawn)
    except asyncio.CancelledError:
     self.process=await spawn
     if self.process.returncode is None:os.killpg(self.process.pid,signal.SIGKILL);await self.process.wait()
     raise
    try:await asyncio.wait_for(self.process.wait(),timeout=300)
    except BaseException:
     if self.process.returncode is None:
      os.killpg(self.process.pid,signal.SIGTERM)
      try:await asyncio.wait_for(self.process.wait(),10)
      except asyncio.TimeoutError:os.killpg(self.process.pid,signal.SIGKILL);await self.process.wait()
     raise
    if self.process.returncode:raise RuntimeError('本地PE失败；详细信息见服务器 PE 日志，未提交视频。')
   result=json.loads(out.read_text());self.update(row['id'],'succeeded',result)
  except asyncio.CancelledError:
   self.update(row['id'],'interrupted',error='改写已停止，未提交视频');raise
  except Exception as exc:self.update(row['id'],'failed',error=str(exc)[:1000])
  finally:
   self.process=None;lock.close()
