"""Official Ref2VA reference budgets and dynamic media graph, no padded inputs."""
import copy
from pathlib import Path
import av
from reference_video import normalize

LIMITS={'images':9,'videos':3,'audios':3,'total_files':12,'video_total_seconds':15,'audio_total_seconds':15,'clip_min_seconds':2,'clip_max_seconds':15}

def media_ids(data,plural,singular):
    if plural in data and singular in data:
        raise ValueError(f'请只使用 {plural} 或 {singular}，不要同时填写。')
    values=data.get(plural, [data[singular]] if data.get(singular) else [])
    if not isinstance(values,list) or not all(isinstance(x,str) and x for x in values):
        raise ValueError(plural+' 必须是按参考顺序排列的素材 ID 数组。')
    return values


def expand(graph,images,videos,audios):
    if len(images)>9 or len(videos)>3 or len(audios)>3 or not 1<=len(images)+len(videos)+len(audios)<=12:
        raise ValueError('参考上限：9 张图片、3 段视频、3 段音频，合计 1–12 个文件。')
    g=copy.deepcopy(graph)
    for key in list(g):
        if g[key]['class_type'] in ('LoadImage','LoadVideo','LoadAudio','GetVideoComponents'):del g[key]
    inputs=g['7']['inputs']
    for key in list(inputs):
        if key.startswith(('ref_images.','ref_videos.','ref_video_audios.','ref_audios.')):del inputs[key]
    def node(n,cls,field,value):g[str(n)]={'class_type':cls,'inputs':{field:value}}
    for i,aid in enumerate(images):
        node(201+i,'LoadImage','image','');inputs[f'ref_images.ref_image_{i}']=[str(201+i),0]
    for i,aid in enumerate(videos):
        node(401+2*i,'LoadVideo','file','');node(402+2*i,'GetVideoComponents','video',[str(401+2*i),0])
        inputs[f'ref_videos.ref_video_{i}']=[str(402+2*i),0]
        # Soundtrack is bound after probing the file, only if it exists.
    for i,aid in enumerate(audios):
        node(301+i,'LoadAudio','audio','');inputs[f'ref_audios.ref_audio_{i}']=[str(301+i),0]
    return g


def check_durations(graph,root):
    totals={'video':0.,'audio':0.}
    for n,item in graph.items():
        cls=item['class_type']
        if cls not in ('LoadVideo','LoadAudio'):continue
        kind='video' if cls=='LoadVideo' else 'audio'
        field='file' if kind=='video' else 'audio'
        path=(root/'input'/item['inputs'][field]).resolve()
        if not path.is_relative_to((root/'input').resolve()):raise ValueError('素材路径越界。')
        if kind=='video':
            path=normalize(path,root)
            item['inputs'][field]=path.relative_to(root/'input').as_posix()
        with av.open(str(path)) as c:
            duration=c.duration/av.time_base if c.duration else 0
            if not 2<=duration<=15:raise ValueError('每段参考视频/音频必须为 2–15 秒。')
            totals[kind]+=duration
            if kind=='video':
                if not c.streams.video:raise ValueError('参考视频缺少视频流。')
                fps=c.streams.video[0].average_rate
                if fps is None or abs(float(fps)-24)>.01:
                    raise ValueError('本机 H3 节点按 24 fps 解释参考帧，请先将参考视频转换为 24 fps。')
                if c.streams.audio:
                    i=(int(n)-401)//2
                    graph['7']['inputs'][f'ref_video_audios.ref_video_audio_{i}']=[str(402+2*i),1]
    for kind,total in totals.items():
        if total>15.000001:raise ValueError(f'{kind} 参考累计 {total:.3f} 秒，超过 15 秒。')
    return totals
