"""Versioned gateway graph construction. Never changes stored jobs or submits prompts."""
import copy
import re
from lora_profiles import PROFILES, apply_profile, resolve_profile

VERSION = '2.14-best-effort-dialogue'
DEFAULT_PROFILE = 'physics'
MODES = {'t2v': '07_fl2va_pdd8', 'i2v': '03_i2v_turbo8',
         'first_last': '04_first_last_turbo8', 'ref_image': '09_ref2va_pdd8',
         'ref_multi': '06_ref_multi_turbo4', 'ref_media': '12_ref_media_pdd8',
         'ref4_audio': '13_ref4_audio_pdd8', 'ref_all':'09_ref2va_pdd8'}
ALIASES = {**{v: k for k,v in MODES.items()},
           '09_ref2va_pdd8':'ref_image','01_t2v_turbo8':'t2v','02_t2v_turbo4':'t2v','05_ref_image_turbo4':'ref_image',
           '08_fl2va_pdd4':'t2v','10_ref2va_pdd4':'ref_image','11_t2v_base20':'t2v'}


def validate_chinese(text):
    if not re.search(r'[\u3400-\u9fff]', text):
        raise ValueError('提示词正文必须为中文，不自动翻译英文稿。')
    cleaned = re.sub(r'<[^>]*>|\[(?:Chinese|Picture[^\]]*|Audio[^\]]*)\]|\(S\d+\)', '', text)
    if re.search(r'(?:\b[A-Za-z]{2,}\b[ ,;:.!?]+){4,}', cleaned):
        raise ValueError('检测到英文自然语言正文，请使用中文；技术标签可保留。')


def validate_prompt(text):
    validate_chinese(text)
    required = [('总体要求或风格', r'总体要求|风格'), ('核心事件', r'核心|事件|剧情'),
                ('节奏', r'节奏'), ('视角或机位', r'视角|机位'), ('音频', r'音频'),
                ('分镜标题', r'【镜头\s*\d+[^】]*】'), ('画面', r'画面'), ('音效', r'音效')]
    missing = [label for label, pattern in required if not re.search(pattern, text)]
    if missing:
        raise ValueError('请按中文分镜格式补充：' + '、'.join(missing))


def resolve(name):
    mode = name if name in MODES else ALIASES.get(name)
    if not mode:
        raise ValueError('未知工作流，请查看 /v1/workflows')
    return mode


def prepare(templates, name, profile):
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ValueError('未知 LoRA 预设，请查看 /v1/profiles')
    mode = resolve(name)
    g = copy.deepcopy(templates[MODES[mode]])
    trunk = 'ref2va' if 'ref2va' in g['1']['inputs']['unet_name'] else 'fl2va'
    if PROFILES[profile].get('fl_only') and trunk != 'fl2va':
        raise ValueError(f'{profile} 仅支持 t2v、i2v、first_last；不能用于参考素材模式。')
    base = templates['09_ref2va_pdd8' if trunk == 'ref2va' else '07_fl2va_pdd8']
    # Keep conditioning, media references, VAE and output wiring; replace the sampling branch.
    for key in ('5','6','8','9'):
        g[key] = copy.deepcopy(base[key])
    g.pop('16', None)
    g['11']['inputs']['sigmas'] = ['6',1]
    return g, mode, trunk


def finish(g, profile, mode, trunk):
    result = apply_profile(g, profile)
    p = resolve_profile(profile, trunk)
    result['15'].setdefault('_meta', {})['gateway_config'] = {
        'architecture': VERSION, 'mode':mode, 'profile':profile, 'title':p['title'],
        'trunk':trunk, 'method':p['method'], 'steps':p['steps'],
        'lora_file':p.get('file'), 'strength':p.get('strength'),
        'attention':p.get('attention', 'pytorch'), 'extra_loras':p.get('extra_loras', []),
        'sampler':result['9']['inputs'].get('sampler_name', result['9']['class_type'])}
    return result


def configure_references(graph, mode, count, audio=False):
    """Expand real Ref2VA inputs, retaining ordered image-to-reference binding."""
    if mode not in ('ref_multi','ref_media'):
        return graph
    if not 1 <= count <= 9:
        raise ValueError('此参考模式支持 1–9 张图片；图片顺序对应参考编号 1–9。')
    g=copy.deepcopy(graph)
    for key in list(g):
        if g[key]['class_type']=='LoadImage':del g[key]
    inputs=g['7']['inputs']
    for key in list(inputs):
        if key.startswith('ref_images.'):del inputs[key]
    for index in range(count):
        node_id=str(201+index)
        g[node_id]={'class_type':'LoadImage','inputs':{'image':''},'_meta':{'title':f'参考图 {index+1}'}}
        inputs[f'ref_images.ref_image_{index}']=[node_id,0]
    if mode=='ref_multi' and audio:
        g['301']={'class_type':'LoadAudio','inputs':{'audio':''},'_meta':{'title':'参考音频 1'}}
        inputs['ref_audios.ref_audio_0']=['301',0]
    return g


def reference_limits(mode):
    return {'t2v':(0,0),'i2v':(1,1),'first_last':(2,2),'ref_image':(1,1),
            'ref_multi':(1,9),'ref_media':(1,9),'ref4_audio':(4,4),'ref_all':(0,9)}[mode]
