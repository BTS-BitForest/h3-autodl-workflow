"""First-boot progress; anonymous downloads, authenticated HTTP access."""
import asyncio,json,os,sys
from pathlib import Path
from aiohttp import web
SUITE=Path(__file__).resolve().parents[1]
ROOT=Path(os.environ.get('H3_ROOT','/root/autodl-tmp/h3'))
def status():
 try:return json.loads((ROOT/'bootstrap/status.json').read_text())
 except (OSError,ValueError):return {'state':'pending','phase':'waiting'}
async def info(req):return web.json_response(status())
async def retry(req):
 proc=await asyncio.create_subprocess_exec(sys.executable,str(SUITE/'scripts/start_download.py'),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
 await proc.wait()
 if proc.returncode:raise web.HTTPInternalServerError(text='下载器启动失败，请查看服务器日志')
 return web.json_response({'started':True})
def ready():
 if status().get('state')!='complete':raise web.HTTPServiceUnavailable(text='模型尚未下载并校验完成，请查看页面上的初始化进度。')
