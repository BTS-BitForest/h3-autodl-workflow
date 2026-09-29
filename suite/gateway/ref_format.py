"""Unified Chinese prompt adapter. Legacy format names are compatibility aliases."""
import re
from architecture import validate_chinese

HEADINGS = {
    'subject_definitions': '人物与参考绑定', 'summary': '核心概要',
    'retention_analysis': '参考保留要求', 'detailed_description': '详细分镜',
    'overall_soundscape': '音频', 'non_diegetic_music': '配乐要求',
}

def normalize_prompt(data, image_count=0, video_count=0, audio_count=0):
    text = data['prompt'].strip()
    # Preserve section contents/order; translate only known interface labels.
    text = re.sub(r'(?mi)^\s*(' + '|'.join(HEADINGS) + r')\s*:',
                  lambda m: HEADINGS[m.group(1).lower()] + '：', text)
    text = re.sub(r'(?mi)^(配乐要求：)\s*N/A\s*$', r'\1禁止背景音乐与配乐。', text)
    text = re.sub(r'(?i)\baudio reuse\b', '沿用参考音频', text)
    text = re.sub(r'(?i)\bno additional voices\b', '禁止额外人声', text)
    for zh, en in [('图片','Picture'), ('音频','Audio'), ('视频','Video')]:
        text = re.sub(r'\[' + zh + r'\s*(\d+)\]', lambda m: '<'+en+' '+m.group(1)+'>', text)
    validate_chinese(text)
    for kind, count in [('Picture',image_count),('Video',video_count),('Audio',audio_count)]:
        indices = [int(n) for n in re.findall(r'<'+kind+r'\s+(\d+)>', text, re.I)]
        if any(n < 1 or n > count for n in indices):
            raise ValueError(f'{kind}参考编号越界：本请求实际接入{count}份该类素材。')
    blocks = list(re.finditer(r'<d>(.*?)</d>', text, re.S))
    if text.count('<d>') != len(blocks) or text.count('</d>') != len(blocks):
        raise ValueError('对白标签未成对闭合，请检查<d>与</d>。')
    if blocks and data.get('sound_policy','no_voice') != 'specified_dialogue':
        raise ValueError('提示词包含对白，请设置sound_policy=specified_dialogue并填写台词和说话人。')
    def canonical(s):
        return re.sub(r'\s+', '', re.sub(r'^\s*\[Chinese\]\s*', '', s, flags=re.I))
    if blocks:
        line = canonical(data.get('dialogue_text',''))
        parts = [canonical(m.group(1)) for m in blocks]
        if len(parts)>1 and all(x==line for x in parts):
            # Keep the first complete dialogue at its original shot position.
            for m in reversed(blocks[1:]): text=text[:m.start()]+text[m.end():]
        elif ''.join(parts) != line:
            raise ValueError('分镜中的对白与dialogue_text不一致，请统一实际台词；接口不会擅自改写。')
        text = re.sub(r'<d>(.*?)</d>', lambda m: '<d>[Chinese] '+re.sub(r'^\s*\[Chinese\]\s*','',m.group(1),flags=re.I).strip()+'</d>', text, flags=re.S)
    warnings=[]
    if not re.search(r'【镜头\s*\d+[^】]*】', text):
        warnings.append('建议使用中文分镜，明确镜头时间、机位、动作、后果及音效；未因标题格式阻止提交。')
    return text, warnings


def validate_ref(data, mode):
    """Compatibility entry; mode no longer imposes editorial section ordering."""
    return normalize_prompt(data, len(data.get('images',[])),
                            len(data.get('videos',[])) or bool(data.get('video')),
                            len(data.get('audios',[])) or bool(data.get('audio')))[0]
