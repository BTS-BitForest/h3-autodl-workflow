"""Read real ComfyUI node progress for the gateway's reserved client ID."""
import asyncio
import json
import time
import aiohttp

class LiveProgress:
    def __init__(self, on_execution=None):
        self.on_execution=on_execution
        self.connected=False
        self.current=None

    def consume(self, event):
        kind=event.get('type');data=event.get('data',{})
        if self.on_execution:self.on_execution(kind,data)
        jid=data.get('prompt_id')
        if not jid:return  # Reconnect's unscoped executing event must not contaminate another job.
        if kind in ('execution_success','execution_error','execution_interrupted'):
            if self.current and self.current['prompt_id']==jid:self.current=None
            return
        if kind=='progress_state':
            active=[(n,v) for n,v in data.get('nodes',{}).items() if v.get('state')=='running']
            if not active:return
            node,value=active[-1]
            data={'prompt_id':jid,'node':node,'value':value.get('value'), 'max':value.get('max')}
            # 0/1 is ComfyUI's unmeasured node-start placeholder.
            if data['max']==1 and data['value']==0:data.update(value=None,max=None)
        elif kind not in ('executing','progress'):
            return
        node=data.get('node')
        if node is None:return
        value,total=data.get('value'),data.get('max')
        if kind=='executing' and self.current and self.current['prompt_id']==jid and self.current['node']==str(node):return
        percent=None
        if isinstance(value,(int,float)) and isinstance(total,(int,float)) and total>0:
            percent=round(min(100,max(0,value/total*100)),1)
        self.current={'prompt_id':jid,'node':str(node),'value':value,'total':total,'percent':percent,'updated':time.time()}

    def for_job(self,jid,graph):
        if not self.current or self.current['prompt_id']!=jid:
            return {'connected':self.connected,'stage':'等待下一次实时进度','percent':None}
        p=dict(self.current);node=graph.get(p['node'],{});kind=node.get('class_type','')
        stages={'KSampler':'视频画风重绘采样','WanVaceToVideo':'逐帧动作条件编码','VideoEnhanceUpscale':'逐帧高清放大','Canny':'提取原视频轮廓','SamplerCustomAdvanced':'音视频采样','VAEDecode':'视频解码','VAEDecodeAudio':'音频解码',
            'CreateVideo':'合成音视频','SaveVideo':'编码与保存视频','UNETLoader':'加载主模型',
            'CLIPLoader':'加载文本编码器','VAELoader':'加载解码器','LoraLoaderModelOnly':'加载 LoRA',
            'MiniMaxH3TurboLoRA':'加载加速 LoRA','MiniMaxH3ImageToVideo':'编码提示词与首尾帧',
            'MiniMaxH3ReferenceToVideo':'编码提示词与参考素材','MiniMaxH3PDDAccApply':'配置 PDD 加速'}
        p.update(connected=self.connected,stage=stages.get(kind,node.get('_meta',{}).get('title',kind or '处理节点 '+p['node'])),sampling=kind in ('SamplerCustomAdvanced','KSampler'))
        return p

    async def run(self,session,backend):
        while True:
            try:
                async with session.ws_connect(backend+'/ws',params={'clientId':'h3-gateway'},heartbeat=20) as ws:
                    self.connected=True
                    self.current=None
                    async for msg in ws:
                        if msg.type==aiohttp.WSMsgType.TEXT:self.consume(json.loads(msg.data))
                        elif msg.type in (aiohttp.WSMsgType.ERROR,aiohttp.WSMsgType.CLOSED):break
            except (aiohttp.ClientError,asyncio.TimeoutError,ValueError):
                pass
            finally:self.connected=False
            await asyncio.sleep(2)
