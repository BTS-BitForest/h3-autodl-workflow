"""Wire actual time-indexed conditioning, not a claim of trained Ref2VA fidelity."""
from PIL import Image

def plan(g,d):
 c=d.get('dialogue_contract') or {}
 if not isinstance(c,dict):raise ValueError('dialogue_contract必须为对象。')
 bindings=c.get('image_bindings',[])
 if not isinstance(bindings,list):raise ValueError('image_bindings必须为数组。')
 anchors=[(i,b) for i,b in enumerate(bindings) if isinstance(b,dict) and b.get('role')=='shot_start']
 if not anchors:return []
 timeline=c.get('shot_timeline')
 if not isinstance(timeline,list) or not timeline:raise ValueError('shot_start须提供shot_timeline（shot_index、start_frame），并为每张图指定frame_index。')
 starts=[]
 for i,s in enumerate(timeline):
  if not isinstance(s,dict) or s.get('shot_index')!=i+1 or type(s.get('start_frame')) is not int:raise ValueError('shot_timeline须按连续镜号排列，start_frame必须为整数。')
  starts.append(s['start_frame'])
 length=g['7']['inputs']['length']
 if starts[0]!=0 or any(x<0 or x>=length for x in starts) or starts!=sorted(set(starts)):raise ValueError('镜头起点须从第0帧开始、严格递增且不超过视频边界。')
 if len(anchors)!=len(timeline):raise ValueError('每个声明的硬切镜头须对应一张shot_start图。')
 result=[]
 for j,(i,b) in enumerate(anchors):
  frame=b.get('frame_index')
  if b.get('shot_index')!=j+1 or type(frame) is not int or frame!=starts[j]:raise ValueError('shot_start图片顺序/目标帧必须逐一对应shot_timeline起始帧，不能落在镜中或镜尾。')
  source=g['7']['inputs'].get(f'ref_images.ref_image_{i}')
  if not isinstance(source,list):raise ValueError('起始帧图片未连接图谱。')
  result.append({'node':str(500+j),'image_index':i,'shot_index':j+1,'frame_index':frame,'source':source})
 return result

def wire(g,d):
 source=['7',0]
 for a in plan(g,d):
  if a['node'] in g:raise ValueError('时间锚点节点编号冲突。')
  g[a['node']]={'class_type':'MiniMaxH3AddGuide','inputs':{'positive':source,'latent':['7',1],'vae':g['7']['inputs']['vae'],'image':a['source'],'frame_idx':a['frame_index']},'_meta':{'title':f"镜头{a['shot_index']}起始帧条件（未验证Ref2VA权重效果）"}}
  source=[a['node'],0]
 g['8']['inputs']['conditioning']=source

def verify(g,d,root):
 result=[];source=['7',0]
 for a in plan(g,d):
  n=g.get(a['node'],{});inputs=n.get('inputs',{})
  expected={'positive':source,'latent':['7',1],'vae':g['7']['inputs']['vae'],'image':a['source'],'frame_idx':a['frame_index']}
  if n.get('class_type')!='MiniMaxH3AddGuide' or inputs!=expected:raise ValueError('AddGuide接线断开或参数不一致：'+a['node'])
  image_node=g[a['source'][0]];p=(root/'input'/image_node['inputs']['image']).resolve()
  if not p.is_relative_to((root/'input').resolve()):raise ValueError('起始帧路径越界。')
  with Image.open(p) as im:
   w,h=im.size;im.verify()
  if min(w,h)<1 or w*h>4096**2:raise ValueError('起始帧像素尺寸无效。')
  result.append({**a,'source_pixels':[w,h],'target_pixels':[g['7']['inputs']['width'],g['7']['inputs']['height']],'resize':'center crop by native AddGuide','model_path':'minimax_keyframes -> minimax_payload.keyframes -> PackedLayout','trained_ref2va_verified':False})
  source=[a['node'],0]
 if result and g['8']['inputs']['conditioning']!=source:raise ValueError('采样引导器未消费最终AddGuide输出。')
 return result
