#!/usr/bin/env python3
"""Idempotent startup, no restart/interrupt/queue-clear commands."""
import fcntl,json,os,socket,subprocess,time,urllib.request,shutil
from pathlib import Path
SUITE=Path(__file__).resolve().parents[1]
RUN=Path(os.environ.get('H3_RUNTIME_ROOT','/root/autodl-tmp/h3'))
PYTHON=SUITE/'venv/bin/python'
PROXIES=('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')
def clean_env():
 e={k:v for k,v in os.environ.items() if k not in PROXIES};e.update(H3_ROOT=str(RUN),H3_GATEWAY_STATE=str(RUN/'gateway/state'),NO_PROXY='*',no_proxy='*',HF_HOME=str(RUN/'cache/huggingface'),HF_HUB_DISABLE_TELEMETRY='1',XDG_CACHE_HOME=str(RUN/'cache'),TORCH_HOME=str(RUN/'cache/torch'),TMPDIR=str(RUN/'temp'),OMP_NUM_THREADS='8',MKL_NUM_THREADS='8');return e
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def port_open(port):
 try:
  with socket.create_connection(('127.0.0.1',port),timeout=1):return True
 except OSError:return False
def json_get(port,path,token=None,data=None):
 req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=data,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'} if token else {})
 with opener.open(req,timeout=5) as r:return json.load(r)
def main():
 if not PYTHON.is_file():raise SystemExit('运行环境尚未安装，请先运行 scripts/install_clean.sh')
 RUN.mkdir(parents=True,exist_ok=True)
 for d in ('input','output','temp','user','logs','cache','gateway/state','models','bootstrap'):(RUN/d).mkdir(parents=True,exist_ok=True)
 lock=(RUN/'startup.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
 for d in ('workflows',):
  link=RUN/d
  if not link.exists():link.symlink_to(SUITE/d,target_is_directory=True)
  elif link.resolve()!=(SUITE/d).resolve():raise SystemExit('运行目录已有不同的 '+d+'，为避免混用已停止启动。')
 model_config={'h3':{'base_path':str(RUN/'models'),'is_default':True,**{d:d for d in ('diffusion_models','text_encoders','vae','loras','pdd_acc')}}}
 # JSON is a valid YAML document; no external dependency required for bootstrapping.
 model_config['h3']['upscale_models']='upscale_models'
 upscale=RUN/'models/upscale_models/video_enhance'
 upscale.mkdir(parents=True,exist_ok=True)
 alias=upscale/'realesr-general-x4v3.pth'
 if not alias.is_symlink() and not alias.exists():alias.symlink_to('../realesr-general-x4v3.pth')
 config=RUN/'extra_model_paths.yaml';config.write_text(json.dumps(model_config))
 ui=RUN/'user/default/workflows/H3'
 ui.mkdir(parents=True,exist_ok=True)
 for template in (SUITE/'workflows/ui').glob('*.json'):
  if not (ui/template.name).exists():shutil.copy2(template,ui/template.name)
 env=clean_env()
 subprocess.run([str(PYTHON),str(SUITE/"scripts/start_download.py")],env=env,check=True)
 if not port_open(8188):
  # GPU check does not allocate model weights. Concurrent launchers share this lock.
  check=subprocess.run([str(PYTHON),'-c','import torch; assert torch.cuda.is_available(), "GPU unavailable"'],env=env,capture_output=True,text=True)
  if check.returncode:raise SystemExit('GPU 不可用；请开启带 GPU 的实例后重新连接。')
  pidfile=RUN/'comfyui.pid'
  if pidfile.exists():
   try:
    pid=int(pidfile.read_text());os.kill(pid,0)
    cmd=Path('/proc',str(pid),'cmdline').read_bytes()
    if str(SUITE/'ComfyUI/main.py').encode() in cmd:raise SystemExit('ComfyUI 正在启动，请稍后重连；没有重启服务。')
   except (ValueError,ProcessLookupError):pass
  with (RUN/'logs/comfyui.log').open('ab') as log:
   args=[str(PYTHON),str(SUITE/'ComfyUI/main.py'),'--listen','127.0.0.1','--port','8188','--extra-model-paths-config',str(config),'--input-directory',str(RUN/'input'),'--output-directory',str(RUN/'output'),'--temp-directory',str(RUN/'temp'),'--user-directory',str(RUN/'user'),'--cache-classic','--disable-api-nodes']
   proc=subprocess.Popen(args,cwd=SUITE/'ComfyUI',env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True);pidfile.write_text(str(proc.pid))
  for _ in range(120):
   if port_open(8188):break
   if proc.poll() is not None:raise SystemExit('ComfyUI 启动失败，请查看 '+str(RUN/'logs/comfyui.log'))
   time.sleep(1)
  else:raise SystemExit('ComfyUI 启动超过 120 秒，进程仍保留，请稍后重连。')
 # Validate existing service without touching its queue.
 q=json_get(8188,'/queue')
 if not isinstance(q.get('queue_running'),list):raise SystemExit('8188 不是预期 ComfyUI 服务，拒绝接管')
 if not port_open(8190):
  with (RUN/'logs/gateway.log').open('ab') as log:subprocess.Popen([str(PYTHON),str(SUITE/'gateway/server.py')],cwd=SUITE/'gateway',env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
  for _ in range(30):
   if port_open(8190):break
   time.sleep(.5)
 token=(RUN/'gateway/state/token.txt').read_text().strip()
 if json_get(8190,'/v1/health',token).get('service')!='h3-gateway':raise SystemExit('8190 不是预期 H3 服务')
 ticket=json_get(8190,'/v1/browser-ticket',token,b'{}')['ticket']
 print('H3_BROWSER_TICKET='+ticket,flush=True)
if __name__=='__main__':main()
