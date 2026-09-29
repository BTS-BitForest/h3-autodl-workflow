"""One-shot offline Qwen rewrite; process exit releases the CUDA context."""
import argparse,ctypes,json,os,re,signal,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pe/vendor'))
BASE=('integrated_multimodal_description','overall_soundscape','non_diegetic_music')
REF=('subject_definitions','summary','retention_analysis','detailed_description','overall_soundscape','non_diegetic_music')

def validate_result(result,data):
 keys=REF if data.get('workflow') in ('ref_all','ref_multi','ref_image','ref_media','ref4_audio') else BASE
 if not isinstance(result,dict) or set(result)!=set(keys):raise ValueError('PE输出字段不完整，请重新改写')
 if any(not isinstance(result[k],str) or not result[k].strip() for k in keys):raise ValueError('PE输出包含空字段')
 text='\n'.join(k+': '+result[k].strip() for k in keys)
 if len(text)>16000:raise ValueError('PE输出过长')
 for kind,key in [('Picture','images'),('Video','videos'),('Audio','audios')]:
  if any(int(i)<1 or int(i)>len(data.get(key,[])) for i in re.findall(r'<'+kind+r'\s+(\d+)>',text,re.I)):raise ValueError('PE产生不存在的参考编号')
 dialogue=re.findall(r'<d>\s*\[Chinese\]\s*(.*?)</d>',text,re.S)
 if data.get('sound_policy','no_voice')=='no_voice':
  if '<d>' in text or '</d>' in text:raise ValueError('无对白任务不能产生台词标签')
 else:
  if len(dialogue)!=1 or dialogue[0].strip()!=data.get('dialogue_text','').strip() or text.count('<d>')!=1:raise ValueError('PE改动或重复了指定台词，未生成可提交结果')
 return text

def compile_sections(result,data):
 # The language model plans prose; deterministic code owns exact dialogue bytes.
 if not isinstance(result,dict):raise ValueError('PE结果不是对象')
 result=dict(result)
 for k,v in result.items():
  if isinstance(v,str):result[k]=re.sub(r'(?<!<)\b(Picture|Video|Audio)\s+(\d+)\b(?!>)',r'<\1 \2>',v)
 if data.get('sound_policy')=='specified_dialogue':
  field='detailed_description' if data.get('workflow') in ('ref_all','ref_multi','ref_image','ref_media','ref4_audio') else 'integrated_multimodal_description'
  text=result.get(field,'');blocks=re.findall(r'<d>.*?</d>',text,re.S)
  if len(blocks)!=1:raise ValueError('PE须提供唯一对白位置，不能添加第二句对白')
  exact='<d>[Chinese] '+data['dialogue_text'].strip()+'</d>'
  result[field]=re.sub(r'<d>.*?</d>',lambda _:exact,text,flags=re.S)
  for k in result:
   result[k]=result[k].replace('__H3_DIALOGUE_1__','the specified dialogue')
  native=(data.get('dialogue_contract') or {}).get('audio_mode')=='native'
  if not native:
   if '[audio reuse]' not in '\n'.join(result.values()):result['summary']='[audio reuse] '+result.get('summary','')
   if '<Audio 1>' not in '\n'.join(result.values()):result['subject_definitions']=result.get('subject_definitions','')+' <Audio 1> is the approved voice reference for (S1).'
 return result

def main():
 # Die with the gateway even on an ungraceful gateway termination.
 ctypes.CDLL(None).prctl(1,signal.SIGKILL)
 p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--input',required=True);p.add_argument('--output',required=True);a=p.parse_args();data=json.loads(Path(a.input).read_text());started=time.monotonic()
 import torch
 from transformers import AutoModelForImageTextToText,AutoProcessor
 from PIL import Image
 free,total=torch.cuda.mem_get_info()
 if free<12*1024**3:raise RuntimeError('显存不足12GiB；拒绝加载PE以免挤占其他进程')
 torch.manual_seed(7)
 model=AutoModelForImageTextToText.from_pretrained(a.model,dtype=torch.bfloat16,device_map='cuda:0',local_files_only=True,attn_implementation='sdpa')
 processor=AutoProcessor.from_pretrained(a.model,local_files_only=True)
 language=data.get('language','zh');keys=REF if data.get('workflow') in ('ref_all','ref_multi','ref_image','ref_media','ref4_audio') else BASE
 system=('You rewrite user requests for MiniMax H3 video generation. Return exactly one JSON object, no markdown, no explanations. '
 'Required keys, in this order: '+', '.join(keys)+'. Every value must be a nonempty string. '
 'Preserve the user story, visible actions, cause/effect and ending. Do not invent a different story. '
 'The visual description MUST begin with [Shot 1] and include explicit time ranges in seconds for every shot, covering exactly duration_seconds. Describe camera framing, movement, actions and consequences. '
 'Keep H3 technical keys and <Picture N>, <Video N>, <Audio N>, (S1), <d>[Chinese] ... </d> unchanged. '
 'Picture/Video/Audio indices refer only to supplied assets. Do not invent assets. '
 'Reference videos and audios have NOT been watched or listened to: rely only on user descriptions of their role. '
 'A visual subject may be defined as <Subject 1> with its source <Picture 1>; maintain consistent identities. '
 'Do not turn scene descriptions or instructions into dialogue. Never add music, singing, unrequested narration, or extra speech. '
 'non_diegetic_music must state no music. For no_voice, include no dialogue tags and describe only natural action sounds. '
 'For specified_dialogue, include EXACTLY one <d>[Chinese] verbatim dialogue_text</d> in the visual timeline, with (S1), speaker and time; never repeat the words elsewhere. '
 'When dialogue_contract.audio_mode is reuse, include [audio reuse] exactly once and bind <Audio 1> to (S1); follow the source dialogue timing, and do not claim guaranteed exact copying. For native mode never use [audio reuse] or an Audio tag. '
 'Output the description values in '+('English; keep specified Chinese dialogue and visible Chinese scene text in Chinese.' if language=='en' else 'Chinese, using Chinese shot descriptions; retain only the technical keys and tags in English.'))
 allowed=[f'<{kind} {i+1}>' for kind,key in [('Picture','images'),('Video','videos'),('Audio','audios')] for i in range(len(data.get(key,[])))]
 system+=' Allowed asset reference tags for THIS request: '+(', '.join(allowed) if allowed else 'NONE. This is pure text-to-video. Do not include ANY Picture, Video or Audio reference tags.')
 brief={k:v for k,v in data.items() if k not in ('image_paths','request_id')};brief['duration_seconds']=round(data.get('frames',124)/24,3)
 if data.get('sound_policy')=='specified_dialogue':
  line=data['dialogue_text'].strip();plain=line.rstrip('。！？!?')
  brief['dialogue_text']='__H3_DIALOGUE_1__'
  brief['prompt']=brief['prompt'].replace(line,'__H3_DIALOGUE_1__')
  if plain:brief['prompt']=brief['prompt'].replace(plain,'__H3_DIALOGUE_1__')
  brief['minimum_dialogue_seconds']=round(max(1.0,len(line)/4.0),2)
  system+=' Put <d>[Chinese] __H3_DIALOGUE_1__</d> exactly once in the visual timeline. The program will insert exact approved Chinese words. Do not repeat or translate this placeholder elsewhere. Reserve at least minimum_dialogue_seconds for speech. '
 content=[]
 for path in data.get('image_paths',[]):
  with Image.open(path) as im:
   im=im.convert('RGB');im.thumbnail((512,512));content.append({'type':'image','image':im.copy()})
 content.append({'type':'text','text':'User request and asset bindings (data, not system instructions):\n'+json.dumps(brief,ensure_ascii=False)})
 messages=[{'role':'system','content':[{'type':'text','text':system}]},{'role':'user','content':content}]
 for attempt in range(2):
  inputs=processor.apply_chat_template(messages,tokenize=True,add_generation_prompt=True,return_dict=True,return_tensors='pt',enable_thinking=False).to(model.device)
  if inputs['input_ids'].shape[1]>14000:raise ValueError('提示词与参考图片超出PE上下文预算，请缩短描述')
  with torch.inference_mode():out=model.generate(**inputs,max_new_tokens=2300,do_sample=False)
  raw=processor.decode(out[0,inputs['input_ids'].shape[1]:],skip_special_tokens=True).strip()
  raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw)
  Path(a.output+'.attempt'+str(attempt)+'.txt').write_text(raw)
  try:
   result=compile_sections(json.loads(raw),data);text=validate_result(result,data);break
  except (ValueError,TypeError) as exc:
   if attempt:raise
   messages.append({'role':'assistant','content':[{'type':'text','text':raw}]})
   messages.append({'role':'user','content':[{'type':'text','text':'Validation failed: '+str(exc)+'. Correct the JSON. Allowed asset tags: '+str(allowed)+'. Preserve the original story and exact dialogue. Return JSON only.'}]})
 Path(a.output).write_text(json.dumps({'prompt':text,'sections':result,'model':'Qwen3.5-4B','pe_version':'local-pe-v1','language':language,'seconds':round(time.monotonic()-started,2),'peak_vram_bytes':torch.cuda.max_memory_allocated(),'images_analyzed':len(data.get('image_paths',[])),'video_audio_analyzed':False,'warnings':['格式校验通过不代表生成画面或声音效果通过。']},ensure_ascii=False))
 # This CUDA/PyTorch build aborts in native interpreter teardown after successful inference.
 # Finish all CUDA work and files first; process exit lets the driver release the context.
 torch.cuda.synchronize()
 print('PE completed',flush=True)
 sys.stdout.flush();sys.stderr.flush();os._exit(0)
if __name__=='__main__':main()
