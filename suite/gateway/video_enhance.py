"""Independent post-processing workflow; shares durable queue, never H3 prompt policy."""
import hashlib,json,math,uuid
from pathlib import Path
from fractions import Fraction
import av
import dialogue_contract as dc

VERSION='1.1-upscale-only'
MODEL_FILES={'upscale':['upscale_models/realesr-general-x4v3.pth']}
def applies(d):return d.get('workflow')=='video_enhance'
def spec():
 return dict(name='video_enhance',operations=['upscale'],default_operation='upscale',
  default_target_height=768,source='video asset ID or source_job_id (succeeded)',
  style='disabled; nonempty style_prompt/style_image rejected',audio='preserve original; never generated',
  motion='original frame count/order/fps; no resampling or diffusion redraw',
  validation_endpoint='/v1/validate',submission_endpoint='/v1/jobs',preflight_required=True,
  max_seconds=15,max_fps=60,max_frames=900,variable_frame_rate=False,
  notes='仅Real-ESRGAN逐帧超分；不改画风，不使用VACE。seed/steps/control_strength兼容旧客户端但不参与超分。')

def metadata(path):
 with av.open(str(path)) as c:
  vs=c.streams.video
  if len(vs)!=1:raise ValueError('处理源须且仅有一个视频流')
  v=vs[0];rate=v.average_rate
  if not rate or not 1<=float(rate)<=60:raise ValueError('源视频须为1–60fps固定帧率')
  width,height=v.width,v.height
  if max(width,height)>4096 or width*height>4096*2160:raise ValueError('源视频超过4K处理范围')
  audio=len(c.streams.audio)
  if audio>1:raise ValueError('目前仅支持零或一个原音轨')
  stamps=[]
  for f in c.decode(v):
   if f.pts is None:raise ValueError('缺少帧时间戳，不能保证动作时序')
   stamps.append(Fraction(f.pts)*f.time_base)
   if len(stamps)>900:raise ValueError('一次最多900帧，请按镜头拆分')
  if not stamps:raise ValueError('空视频')
  tol=max(float(v.time_base)*2,0.0001)
  if any(abs(float(t-stamps[0]-i/rate))>tol for i,t in enumerate(stamps)):
   raise ValueError('暂不接收可变帧率视频，不静默插帧或变速')
  duration=len(stamps)/float(rate)
  if duration>15.05:raise ValueError('单次处理最多15秒，请按原镜头拆分')
  # Audio/video offsets cannot be silently normalized by GetVideoComponents.
  if audio:
   a=c.streams.audio[0]
   if a.start_time is not None and abs(float(a.start_time*a.time_base-stamps[0]))>1/float(rate):
    raise ValueError('原声画起始时间差超过一帧，请先对齐源文件')
 return dict(width=width,height=height,frames=len(stamps),fps=float(rate),
             fps_fraction=str(rate),duration_seconds=duration,has_audio=bool(audio))

def build(d,db,root):
 allowed={'workflow','request_id','video','source_job_id','operation','style_prompt','style_image',
          'target_height','seed','steps','control_strength','validation_token'}
 if set(d)-allowed:raise ValueError('video_enhance未知字段：'+','.join(sorted(set(d)-allowed)))
 try:jid=str(uuid.UUID(d['request_id']))
 except (KeyError,ValueError,TypeError,AttributeError):raise ValueError('request_id须为UUID')
 if bool(d.get('video'))==bool(d.get('source_job_id')):raise ValueError('video与source_job_id须且只能提供一个')
 operation=d.get('operation','upscale')
 if operation!='upscale':raise ValueError('改画风已停用，本接口仅支持upscale；请删除画风参数后重新预检')
 height=d.get('target_height',768)
 if type(height)is not int or height<256 or height>2160 or height%2:raise ValueError('target_height须为256–2160的偶数，默认768')
 seed=d.get('seed',7);steps=d.get('steps',20);strength=d.get('control_strength',1.0)
 if type(seed)is not int or not 0<=seed<2**64:raise ValueError('seed须为无符号64位整数')
 if type(steps)is not int or not 1<=steps<=50:raise ValueError('steps须为1–50')
 if type(strength)not in (int,float) or not math.isfinite(strength) or not .5<=strength<=2:raise ValueError('control_strength须为0.5–2')
 prompt=d.get('style_prompt','')
 if not isinstance(prompt,str) or len(prompt)>16000:raise ValueError('style_prompt须为不超过16000字符的字符串')
 prompt=prompt.strip()
 if prompt or d.get('style_image'):raise ValueError('本接口已改为仅超分，请删除style_prompt和style_image；不再提供改画风')
 records=[]
 def asset(aid,kind):
  if not isinstance(aid,str):raise ValueError('资产ID须为字符串')
  row=db.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
  if not row or row['kind']!=kind:raise ValueError('缺失或类型错误的素材：'+aid)
  path=(root/'input'/row['path']).resolve()
  if not path.is_relative_to((root/'input').resolve()) or not path.is_file():raise ValueError('素材路径无效')
  digest=dc.file_sha(path)
  if digest!=row['sha256']:raise ValueError('素材SHA256发生变化')
  records.append(dict(asset_id=aid,kind=kind,path=row['path'],sha256=digest))
  return path,row['path']
 if d.get('video'):source,load_path=asset(d['video'],'video')
 else:
  sid=d['source_job_id']
  if not isinstance(sid,str):raise ValueError('source_job_id须为字符串')
  row=db.execute('SELECT * FROM jobs WHERE id=?',(sid,)).fetchone()
  if not row or row['state']!='succeeded':raise ValueError('source_job_id必须是已成功且原片存在的任务')
  result=json.loads(row['result'])
  source=(root/'output'/result['file']).resolve()
  if not source.is_relative_to((root/'output').resolve()) or not source.is_file():raise ValueError('源任务原片缺失')
  load_path=source.relative_to(root/'output').as_posix()+' [output]'
  records.append(dict(source_job_id=sid,kind='video',path=load_path,sha256=dc.file_sha(source)))
 source_meta=metadata(source)
 width=max(16,2*round(source_meta['width']/source_meta['height']*height/2))
 if width>4096:raise ValueError('按比例输出宽度超过4096，请降低target_height')
 required=MODEL_FILES['upscale']
 missing=[f for f in required if not (root/'models'/f).is_file()]
 g={}
 def node(i,kind,**inputs):g[str(i)]={'class_type':kind,'inputs':inputs}
 node(1,'LoadVideo',file=load_path)
 node(2,'GetVideoComponents',video=['1',0])
 source_frames=['2',0]
 n=source_meta['frames']
 node(31,'UpscaleModelLoader',model_name='video_enhance/realesr-general-x4v3.pth')
 node(32,'VideoEnhanceUpscale',images=source_frames,upscale_model=['31',0],width=width,height=height)
 node(14,'CreateVideo',images=['32',0],fps=['2',2])
 if source_meta['has_audio']:g['14']['inputs']['audio']=['2',1]
 node(15,'SaveVideo',video=['14',0],filename_prefix='H3/gateway/'+jid,format='mp4',codec={'codec':'h264','encoding':{'encoding':'re-encode','crf':18.0}})
 risks=['局部细节由超分模型重建，可能闪烁']
 config=dict(architecture=VERSION,mode='video_enhance',profile=operation,source=source_meta,
  output={'width':width,'height':height,'frames':n,'fps':source_meta['fps']},
  effective_prompt=prompt,audio_policy='source audio stream copy after encoding' if source_meta['has_audio'] else 'source silent; output silent',
  frame_policy='original count/order/fps; no padding or interpolation',
  prompt_warnings=risks,required_models=required,missing_models=missing)
 g['15']['_meta']={'gateway_config':config}
 audit=dict(control_level='frame_preserving',input_integrity='passed',
  submission_allowed=not missing,unverified_risks=risks,assets=records,
  request_sha256=dc.sha({k:v for k,v in d.items() if k!='validation_token'}),
  prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
  workflow_sha256=dc.sha(g))
 config['enhance_audit']=audit
 return g

def inspect_and_preserve(path,graph,root):
 """Keep source compressed audio packets; verify frame dimensions/time, no creative verdict."""
 import subprocess
 c=graph['15']['_meta']['gateway_config'];audit=c['enhance_audit']
 r=audit['assets'][0];ref=r['path'];is_output=ref.endswith(' [output]')
 source=(root/('output' if is_output else 'input')/(ref[:-9] if is_output else ref)).resolve()
 if dc.file_sha(source)!=r['sha256']:raise ValueError('源视频在提交后发生变化，拒绝回填音轨')
 expected=c['output']
 if c['source']['has_audio']:
  tmp=path.with_name(path.stem+'.audio-copy.mp4')
  try:
   p=subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-i',str(path),'-i',str(source),'-map','0:v:0','-map','1:a:0','-c','copy','-map_metadata','0','-movflags','+faststart',str(tmp)],capture_output=True,text=True,timeout=180)
   if p.returncode:raise ValueError('原音轨无损封装失败（不自动改为静音或重合成）：'+p.stderr[-800:])
   tmp.replace(path)
  finally:tmp.unlink(missing_ok=True)
 actual=metadata(path)
 if (actual['width'],actual['height'],actual['frames'])!=(expected['width'],expected['height'],expected['frames']) or abs(actual['fps']-expected['fps'])>1e-6 or actual['has_audio']!=c['source']['has_audio']:
  raise ValueError('处理输出尺寸/帧数/帧率/音轨与源时序约定不一致')
 return dict(bytes=path.stat().st_size,
  audio_source='original source stream copied' if actual['has_audio'] else 'source has no audio',
  **actual)
