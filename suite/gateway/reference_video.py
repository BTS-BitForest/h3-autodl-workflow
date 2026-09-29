"""Create a CFR reference derivative without changing uploaded originals."""
import fcntl,hashlib,os,subprocess,uuid
from pathlib import Path
import av

def normalize(path,root):
 path=Path(path).resolve();base=(Path(root)/'input').resolve()
 if not path.is_relative_to(base):raise ValueError('参考视频路径越界')
 with av.open(str(path)) as c:
  v=c.streams.video[0];rate=v.average_rate
  if rate is not None and abs(float(rate)-24)<.01:return path
  duration=c.duration/av.time_base if c.duration else 0
  if not 2<=duration<=15 or v.width*v.height>1920*1080:raise ValueError('参考视频须为2–15秒且不超过1920×1080')
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 folder=base/'gateway_normalized';folder.mkdir(exist_ok=True)
 target=folder/(h.hexdigest()+'_24fps_v1.mp4')
 with target.with_suffix('.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  if not target.exists():
   temp=folder/(uuid.uuid4().hex+'.tmp.mp4')
   try:
    proc=subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(path),'-map','0:v:0','-map','0:a:0?','-vf','fps=24','-c:v','libx264','-preset','fast','-crf','18','-threads','2','-c:a','aac','-b:a','192k','-movflags','+faststart',str(temp)],capture_output=True,timeout=120)
    if proc.returncode:raise ValueError('参考视频24fps转换失败：'+proc.stderr.decode(errors='replace')[-400:])
    with av.open(str(temp)) as c:
     if float(c.streams.video[0].average_rate)!=24 or abs(c.duration/av.time_base-duration)>.15:raise ValueError('参考视频转换后时长/帧率校验失败')
    os.replace(temp,target)
   finally:temp.unlink(missing_ok=True)
 return target
