"""Fail-closed specified-dialogue contract; no model execution or prompt rewriting."""
import base64,copy,hashlib,hmac,json,math,re,uuid
from pathlib import Path
import av
import temporal_guides

CAPABILITIES={'forced_voice_ownership':False,'forced_silent_lips':False,
              'shot_start_frame_lock':False,'separate_identity_reference':False,
              'native_audio_reference':True,'prompt_passthrough':True}
# Distinguish running graph support from candidate nodes and prompt semantics.
CAPABILITY_DETAILS = {
 'scope':'当前网关Ref2VA执行图；不是对H3所有潜在能力的判定',
 'native_audio_reference':{'status':'wired','path':'LoadAudio -> 7.ref_audios -> audio_vae.encode -> minimax_refs -> minimax_payload.refs',
   'guarantees':'资产完整性与真实条件绑定；不保证逐字复现'},
 'voice_ownership':{'status':'prompt_only','forced':False,
   'note':'原文可表达画外声；当前没有独立说话人归属或强制闭口控制。结构化声明不等于模型控制。'},
 'image_references':{'status':'wired','path':'LoadImage -> 7.ref_images -> vae.encode -> minimax_refs',
   'effective_role':'generic_reference','shot_start_lock':False,'identity_lock':False},
 'timed_image_audio_guide':{'status':'graph_wired_cpu_verification_only',
   'node':'MiniMaxH3AddGuide','path':'minimax_keyframes -> minimax_payload.keyframes -> PackedLayout',
   'note':'shot_start已接入图像AddGuide时间条件；无GPU测试不验证训练权重效果。音频AddGuide未接入，不宣称锁首帧或强制声音。'},
 'audio_offset_seconds':{'status':'duration_validation_only',
   'note':'当前用于核对可用时长，不是已实现的强制入声时间控制。'},
 'submission_control':{'default':'hard','best_effort_available':True,
   'reason':'显式best_effort允许真实条件提交，效果风险由用户审片；输入完整性仍严格检查。'},
}
def strict(d):return d.get('prompt_format') in ('ref2va_v1','pe_v1','raw_v1') and d.get('sound_policy')=='specified_dialogue'
def native_dialogue(d):
 c=d.get('dialogue_contract') or {}
 mode=c.get('audio_mode','reuse')
 if mode not in ('reuse','native'):raise ValueError('Unsupported dialogue audio_mode')
 if mode!='native':return False
 if c.get('native_dialogue_approved') is not True or not c.get('approval_reference'):
  raise ValueError('Native dialogue requires explicit approval declaration')
 if d.get('audio') or d.get('audios') or d.get('video') or d.get('videos'):
  raise ValueError('Native dialogue mode cannot contain audio/video reference assets')
 if c.get('voice_type')!='onscreen_dialogue':raise ValueError('Native dialogue supports onscreen speaker only')
 if d.get('control_level')!='best_effort':raise ValueError('Native dialogue exactness cannot be guaranteed; use explicit best_effort')
 return True
def sha(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def file_sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def prompt_check(d):
 p=d['prompt'].strip()
 blocks=re.findall(r'<d>\s*\[Chinese\]\s*(.*?)\s*</d>',p,re.S)
 if p.count('<d>')!=1 or p.count('</d>')!=1 or len(blocks)!=1 or blocks[0].strip()!=d['dialogue_text'].strip():
  raise ValueError('指定对白必须且只能有一个<d>[Chinese] ... </d>，逐字匹配dialogue_text；仅忽略两侧空白。')
 native=native_dialogue(d)
 if native and ('[audio reuse]' in p or re.search(r'<Audio\s+\d+>',p)):
  raise ValueError('Native dialogue cannot claim audio reuse or nonexistent audio assets')
 if not native and (p.count('[audio reuse]')!=1 or not p.count('<Audio 1>') or any(t!='<Audio 1>' for t in re.findall(r'<Audio\s+\d+>',p))):
  raise ValueError('获批音频复用必须保留唯一的[audio reuse]；只允许引用同一<Audio 1>，可跨镜重复引用。')
 if re.search(r'no_voice|(?:全片|全程)无人声|(?:^|[。；\n])\s*无人声|禁止任何人声|禁止所有人声|全程静音|\bno voices?\b|\bno speech\b',p,re.I):
  raise ValueError('指定对白与全局禁声指令冲突；请只禁止额外人声。')
 return p

def graph_hash(g):
 g=copy.deepcopy(g);g.get('15',{}).get('_meta',{}).get('gateway_config',{}).pop('dialogue_audit',None)
 return sha(g)

def audit(g,d,db,root,slots):
 p=prompt_check(d)
 level=d.get('control_level','hard')
 if level not in ('hard','best_effort'):raise ValueError('control_level须为hard或best_effort。')
 if g['7']['inputs']['prompt']!=p:raise ValueError('接口改写了指定对白prompt，禁止提交。')
 c=d.get('dialogue_contract')
 if not isinstance(c,dict):raise ValueError('缺少dialogue_contract：须声明获批音频、voice_type、audio_offset_seconds和image_bindings。')
 voice=c.get('voice_type')
 if voice not in ('onscreen_dialogue','offscreen_dialogue','memory_voice'):raise ValueError('voice_type须为onscreen_dialogue、offscreen_dialogue或memory_voice。')
 offset=c.get('audio_offset_seconds')
 if isinstance(offset,bool) or not isinstance(offset,(int,float)) or not math.isfinite(offset) or offset<0:raise ValueError('audio_offset_seconds须为有限非负秒数。')
 audio_ids=d.get('audios', [d['audio']] if d.get('audio') else [])
 native=native_dialogue(d)
 if len(audio_ids)!=(0 if native else 1):raise ValueError('指定对白参考音频数量与声明模式不符。')
 if d.get('video') or d.get('videos'):raise ValueError('指定对白路径暂不接受可能引入第二声源的视频参考；请使用图片及唯一获批音频。')
 approved=c.get('approved_audio',{})
 if not native and (not isinstance(approved,dict) or approved.get('asset_id')!=audio_ids[0] or approved.get('approved') is not True or not approved.get('approval_reference')):
  raise ValueError('approved_audio须声明匹配的asset_id、sha256、approved=true及approval_reference；接口记录声明，不冒充人工审批。')
 records=[]
 for node,kind,field in slots:
  inputs=g[node]['inputs'];path=(root/'input'/inputs[field]).resolve()
  if not path.is_relative_to((root/'input').resolve()) or not path.is_file():raise ValueError('参考素材缺失或路径越界。')
  # Look up by request order, never infer approval from merely having a file.
  ids=d.get('images',[]) if kind=='image' else audio_ids
  idx=sum(x['kind']==kind for x in records)
  if idx>=len(ids):raise ValueError('素材数量与加载节点不一致。')
  aid=ids[idx];row=db.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
  actual=file_sha(path)
  if row is None or row['path']!=inputs[field] or row['sha256']!=actual:raise ValueError('资产哈希或绑定变化：'+aid)
  key=f'ref_images.ref_image_{idx}' if kind=='image' else f'ref_audios.ref_audio_{idx}'
  if g['7']['inputs'].get(key)!=[node,0]:raise ValueError('素材加载节点没有实际接入生成节点：'+aid)
  record=dict(asset_id=aid,kind=kind,sha256=actual,loader_node=node,conditioning_input='7.inputs.'+key)
  if kind=='audio':
   if approved.get('sha256')!=actual:raise ValueError('获批音频SHA-256与实际文件不同。')
   with av.open(str(path)) as container:
    if not container.streams.audio or container.duration is None:raise ValueError('音频无法读取实际时长。')
    duration=container.duration/av.time_base
   if not math.isfinite(duration) or duration<=0:raise ValueError('无效音频时长。')
   record.update(duration_seconds=duration,speaker=d['dialogue_speaker'],approval_reference=approved['approval_reference'])
  records.append(record)
 images=[r for r in records if r['kind']=='image'];bindings=c.get('image_bindings')
 if not isinstance(bindings,list) or len(bindings)!=len(images):raise ValueError('image_bindings须按上传顺序覆盖每张参考图，包含asset_id、sha256、shot_index、role。')
 temporal=temporal_guides.verify(g,d,root)
 reasons=[];hard_only=[];risks=[]
 if voice in ('offscreen_dialogue','memory_voice'):risks.append('memory_voice_attribution' if voice=='memory_voice' else 'offscreen_voice_attribution')
 if c.get('silent_on_screen'):risks.append('silent_lips')
 if voice in ('offscreen_dialogue','memory_voice') or c.get('silent_on_screen'):
  reasons.append('当前无法强制画外声口型归属或画内人物闭口；H3节点没有相应独立控制通路。')
 for i,(rec,b) in enumerate(zip(images,bindings)):
  if not isinstance(b,dict) or b.get('asset_id')!=rec['asset_id'] or b.get('sha256')!=rec['sha256'] or type(b.get('shot_index')) is not int or b['shot_index']<1 or b.get('role') not in ('shot_start','identity','identity_reference','reference'):
   raise ValueError('图片绑定/哈希/镜头索引不一致：Picture '+str(i+1))
  rec.update(picture_index=i+1,shot_index=b['shot_index'],requested_role=b['role'],effective_role='generic_reference')
  if b['role']=='shot_start':
   rec['effective_role']='timed_guide_and_reference'
   rec['frame_index']=b['frame_index']
   risks.append('shot_start_visual_fidelity')
   reasons.append('已接入AddGuide时间条件，但Ref2VA权重对此条件的效果尚未验证，不能宣称起始帧硬锁：Picture '+str(i+1))
  if b['role']=='identity':
   hard_only.append('当前未支持独立身份硬锁：Picture '+str(i+1))
  if b['role']=='identity_reference':
   if b.get('approved') is not True or not b.get('approval_reference'):raise ValueError('角色身份参考须提供审定声明approved与approval_reference。')
   rec.update(effective_role='identity_reference',identity_lock=False,approval_reference=b['approval_reference'])
 reasons.extend(hard_only)
 risks.extend(['dialogue_exactness','identity_drift'])
 duration=0 if native else next(r['duration_seconds'] for r in records if r['kind']=='audio')
 seconds=g['7']['inputs']['length']/24
 if offset+duration>seconds+1e-6:raise ValueError(f'视频时长不足：需{offset+duration:.6f}秒，当前{seconds:.6f}秒。')
 return dict(effective_prompt=p,text_node_path='7.inputs.prompt',assets=records,temporal_guides=temporal,capabilities=CAPABILITIES,capability_details=CAPABILITY_DETAILS,
   execution={'gateway_version':g['15']['_meta']['gateway_config']['architecture'], 'seed':g['10']['inputs']['noise_seed'],
       'width':g['7']['inputs']['width'],'height':g['7']['inputs']['height'],'frames':g['7']['inputs']['length'],
       'models_and_loras':[{ 'node':n,'class_type':v['class_type'],'parameters':{k:x for k,x in v['inputs'].items() if k in ('unet_name','lora_name','pdd_file','strength_model','lora_strength','head_strength','steps','nfe')}} for n,v in g.items() if any(k in v['inputs'] for k in ('unet_name','lora_name','pdd_file'))]},
   policy={'voice_type':voice,'speaker':d['dialogue_speaker'],'audio_mode':'native' if native else 'reuse','native_dialogue_approval':c.get('approval_reference') if native else None,'silent_on_screen':c.get('silent_on_screen',[]),'prompt_unchanged':True,'approval_is_caller_declaration':True},
   duration={'video_seconds':seconds,'audio_seconds':duration,'offset_seconds':offset,'fits':True},
   control_level=level,input_integrity='passed',unverified_risks=list(dict.fromkeys(risks)),
   submission_allowed=not (reasons if level=='hard' else hard_only),unsupported_reasons=list(dict.fromkeys(reasons if level=='hard' else hard_only)),
   control_limitations=list(dict.fromkeys(reasons)),
   request_sha256=sha({k:v for k,v in d.items() if k!='validation_token'}),prompt_sha256=hashlib.sha256(p.encode()).hexdigest(),
   workflow_sha256=graph_hash(g),review_status='待用户审片；接口检查不保证对白完整、人物闭口或脸模一致')

def make_token(a,secret):
 snapshot={k:a[k] for k in ('request_sha256','prompt_sha256','workflow_sha256','assets')}
 body=base64.urlsafe_b64encode(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).decode()
 return body+'.'+hmac.new(secret.encode(),body.encode(),hashlib.sha256).hexdigest()
def verify_token(token,a,secret):
 if not isinstance(token,str):raise ValueError('请先调用/v1/validate，再携带返回的validation_token提交。')
 try:
  body,sig=token.rsplit('.',1)
  if not hmac.compare_digest(sig,hmac.new(secret.encode(),body.encode(),hashlib.sha256).hexdigest()):raise ValueError()
  old=json.loads(base64.urlsafe_b64decode(body))
 except Exception:raise ValueError('无效validation_token，请重新预检。')
 changed=[k for k in ('request_sha256','prompt_sha256','workflow_sha256','assets') if old.get(k)!=a[k]]
 if changed:raise ValueError('预检后发生变化，拒绝提交，请重新预检：'+', '.join(changed))
