"""终章微电影 LoRA 预设：修改执行图，不提交生成任务。"""
import copy
from pathlib import Path

MODEL_DIR = Path('/root/autodl-tmp/h3/models/loras')
PROFILES = {
    'pdd_turbo': dict(title='PDD8＋Ref Turbo8叠加测速（试验）', method='pdd_turbo', steps=8, experimental=True),
    'pdd_sage': dict(title='PDD8＋SageAttention测速', method='pdd', steps=8, attention='sage'),
    'pdd_stack': dict(title='PDD8＋五项动作画质组合（试验）', method='pdd', steps=8, file='Motion_Repair.safetensors', strength=0.3, extra_loras=[dict(file='wushu_spatial_physics_clean_3000_pruned.safetensors', strength=0.2), dict(file='better_motion_h3_lora_v1_500.safetensors', strength=0.4), dict(file='Minimax H3真实电影质感V0.1.safetensors', strength=0.25), dict(file='h3-realism-people-t2v-i2v-r2v.safetensors', strength=0.3)], experimental=True),
    'turbo_weapon': dict(title='Turbo 8步＋武器战斗V1', method='turbo_auto', steps=8, extra_loras=[dict(file='Bunny_weapon_combatV1.safetensors', strength=0.45)], trigger='BUNNY'),
    'turbo': dict(title='Turbo 自动匹配 · FL2VA / Ref2VA 8步 768p', method='turbo_auto', steps=None, steps_by_trunk={'fl2va':8,'ref2va':8}),
    'turbo4': dict(title='Turbo 4步对照', method='turbo_auto4', steps=4),
    'combat': dict(title='近身战斗 V2 · 20步＋SageAttention', method='base', steps=20, attention='sage', file='H3_Combat_V2.safetensors', strength=0.7),
    'motion_repair': dict(title='动作连续性修复 · 20步＋SageAttention', method='base', steps=20, attention='sage', file='Motion_Repair.safetensors', strength=0.9),
    'combat_repair': dict(title='近身战斗＋动作连续性 · 20步＋SageAttention', method='base', steps=20, attention='sage', file='H3_Combat_V2.safetensors', strength=0.7, extra_loras=[dict(file='Motion_Repair.safetensors', strength=0.6)]),
    'pdd_original': dict(title='原方案对照', method='pdd', steps=8),
    'realism': dict(title='人物写实', method='pdd', steps=8, file='h3-realism-people-t2v-i2v-r2v.safetensors', strength=0.6, trigger='r34l1sm'),
    'weapon': dict(title='武器交锋（独立动作 LoRA）', method='base', steps=20, file='Bunny_weapon_combatV1.safetensors', strength=0.45, trigger='BUNNY'),
    'human_motion': dict(title='人体动作（独立测试）', method='base', steps=20, file='better_motion_h3_lora_v1_500.safetensors', strength=0.5, fl_only=True),
    'physics': dict(title='物体空间物理 · 20步＋SageAttention', method='base', steps=20, attention='sage', file='wushu_spatial_physics_clean_3000_pruned.safetensors', strength=0.3),
    'cinema': dict(title='电影质感（实验）', method='base', steps=20, file='Minimax H3真实电影质感V0.1.safetensors', strength=0.35),
    'larry8': dict(title='Larry v4 8步对照', method='larry', steps=8, file='minimax_h3_turbo_v4_step600_ema.safetensors', strength=1.0, fl_only=True),
    'lightx8': dict(title='LightX2V 8步对照', method='lightx', steps=8, file='minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors', strength=1.0, fl_only=True),
}


PROFILES['pdd_sage_stack'] = {**copy.deepcopy(PROFILES['pdd_stack']), 'title':'PDD8＋SageAttention＋六项动作画质', 'attention':'sage'}
PROFILES['pdd_sage_stack']['extra_loras'].append(dict(file='Bunny_weapon_combatV1.safetensors', strength=0.45))

# Strict quality comparison for shots previously generated with pdd_sage_stack.
# Keep the same LoRA stack and SageAttention, but replace PDD acceleration with
# the unaccelerated base sampler at 16 steps. The PDD node itself only supports
# 4/6/8 NFE, so a real 16-step comparison must use the base sampling branch.
PROFILES['base16_sage_stack'] = copy.deepcopy(PROFILES['pdd_sage_stack'])
PROFILES['base16_sage_stack'].update(
    title='基础16步＋SageAttention＋六项动作画质（PDD8严格对照）',
    method='base',
    steps=16,
    experimental=True,
)

def resolve_profile(profile, trunk):
    p = copy.deepcopy(PROFILES[profile])
    if p['method'] in ('turbo_auto', 'turbo_auto4'):
        four = p['method'] == 'turbo_auto4'
        ref = trunk == 'ref2va'
        p.update(method='lightx', steps=4 if four else 8, strength=1.0,
                 shift_video=12.0 if ref and four else 6.0,
                 file=('minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors' if four else 'minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors') if ref else ('minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors' if four else 'minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors'))
    if p['method'] == 'pdd_turbo':
        p.update(method='pdd', strength=1.0, file='minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors' if trunk=='ref2va' else 'minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors')
    return p


def node(kind, title, **inputs):
    return {'class_type': kind, 'inputs': inputs, '_meta': {'title': title}}


def apply_profile(graph, profile, check_files=True):
    """只接受本项目的原始 PDD 图，避免对已改图重复叠加。"""
    g = copy.deepcopy(graph)
    if g.get('6', {}).get('class_type') != 'MiniMaxH3PDDAccApply':
        raise ValueError('请从原始 PDD 工作流选择预设，不要重复叠加。')
    if any('Lora' in n['class_type'] or 'LoRA' in n['class_type'] for n in g.values()):
        raise ValueError('输入图已有 LoRA，请从原始工作流创建。')
    trunk = 'ref2va' if 'ref2va' in g['1']['inputs']['unet_name'] else 'fl2va'
    p = resolve_profile(profile, trunk)
    if p.get('fl_only') and trunk != 'fl2va':
        raise ValueError(f'{profile} 当前预设限 FL2VA；不能把 FL2VA 加速权重套到 Ref2VA。')
    if p.get('file') and check_files and not (MODEL_DIR / p['file']).is_file():
        raise FileNotFoundError(MODEL_DIR / p['file'])
    source = ['1', 0]
    method = p['method']
    if p.get('file') and method not in ('larry', 'lightx'):
        g['100'] = node('LoraLoaderModelOnly', p['title'], model=source,
                        lora_name=p['file'], strength_model=p['strength'])
        source = ['100', 0]
    for index, extra in enumerate(p.get('extra_loras', [])):
        if check_files and not (MODEL_DIR / extra['file']).is_file():
            raise FileNotFoundError(MODEL_DIR / extra['file'])
        key = str(101 + index)
        g[key] = node('LoraLoaderModelOnly', '动作连续性辅助', model=source,
                      lora_name=extra['file'], strength_model=extra['strength'])
        source = [key, 0]
    if p.get('attention') == 'sage':
        g['110'] = node('H3SpeedABSage', 'SageAttention 加速', model=source)
        source = ['110', 0]
    g['5']['inputs']['model'] = source
    if method != 'pdd':
        del g['6']
        model = ['5', 0]
        if method == 'larry':
            g['6'] = node('MiniMaxH3TurboLoRA', p['title'], model=model,
                          lora_name=p['file'], strength=p['strength'], low_vram=False)
            g['9'] = node('MiniMaxH3TurboSampler', 'Larry 专用音视频采样器')
            model = ['6', 0]
        elif method == 'lightx':
            g['6'] = node('LoraLoaderModelOnly', p['title'], model=model,
                          lora_name=p['file'], strength_model=p['strength'])
            g['5']['inputs']['shift_video'] = p.get('shift_video', 6.0)
            g['5'].setdefault('_meta', {})['title'] = f"音视频 Sigma Shift / {g['5']['inputs']['shift_video']} : {g['5']['inputs']['shift_audio']}"
            g['9'] = node('KSamplerSelect', 'LightX2V 采样器', sampler_name='euler')
            model = ['6', 0]
        else:
            g['9'] = node('KSamplerSelect', '未加速基准采样器', sampler_name='res_multistep')
        g['8']['inputs']['model'] = model
        g['16'] = node('BasicScheduler', f"{p['steps']} 步独立调度", model=model,
                       scheduler='simple', steps=p['steps'], denoise=1.0)
        g['11']['inputs']['sigmas'] = ['16', 0]
    trigger = p.get('trigger')
    if trigger:
        g['7']['inputs']['prompt'] = f'LoRA 技术触发标签：{trigger}\n' + g['7']['inputs']['prompt']
    g['15']['inputs']['filename_prefix'] += '_lora_' + profile
    return g
