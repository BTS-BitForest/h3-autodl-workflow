"""Authenticated, durable submission bridge to the existing single ComfyUI queue."""
import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import re
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from urllib.parse import unquote

import aiohttp
from aiohttp import web
import av
from PIL import Image
from live_progress import LiveProgress
from job_timing import JobTiming
from reference_media import LIMITS, media_ids, expand, check_durations
from architecture import configure_references, reference_limits
from architecture import VERSION, DEFAULT_PROFILE, MODES, PROFILES, prepare, finish, validate_chinese
from ref_format import normalize_prompt
import dialogue_contract as dc
import bootstrap_status
import temporal_guides
import video_enhance as ve
from pe_service import PE
from pe_worker import validate_result as validate_pe_result

ROOT = Path(os.environ.get('H3_ROOT', '/root/autodl-tmp/h3'))
STATE = Path(os.environ.get('H3_GATEWAY_STATE', str(ROOT / 'gateway/state')))
BACKEND = os.environ.get('H3_COMFY_URL', 'http://127.0.0.1:8188').rstrip('/')
SOUND = ('仅生成与可见动作同步的原生现场环境与物理声音。禁止背景音乐、配乐、旋律及乐器声；'
         '禁止任何人声，包括对白、旁白、解说、画外音、歌声、哼唱和吟唱。'
         '保留自然远近、强弱和安静间隙，不生成持续响亮的嘶声或静电噪声。')
TYPES = {'LoadImage': ('image', 'image'), 'LoadVideo': ('video', 'file'), 'LoadAudio': ('audio', 'audio')}
SUFFIX = {'image': {'.png', '.jpg', '.jpeg', '.webp'}, 'video': {'.mp4', '.mov', '.webm'}, 'audio': {'.wav', '.mp3', '.flac', '.m4a', '.ogg'}}
TERMINAL = ('succeeded', 'failed', 'interrupted')
MAX_UPLOAD = 512 * 1024**2


def sound_policy(data):
    policy = data.get('sound_policy', 'no_voice')
    if policy == 'no_voice':
        if 'dialogue_text' in data or 'dialogue_speaker' in data:
            raise ValueError('Dialogue requires sound_policy=specified_dialogue')
        return SOUND
    if policy != 'specified_dialogue':
        raise ValueError('Unknown sound_policy')
    line, speaker = data.get('dialogue_text'), data.get('dialogue_speaker')
    if not isinstance(line, str) or not 1 <= len(line.strip()) <= 1000:
        raise ValueError('Specified dialogue requires dialogue_text (1-1000 characters)')
    if not isinstance(speaker, str) or not 1 <= len(speaker.strip()) <= 100:
        raise ValueError('Specified dialogue requires dialogue_speaker (1-100 characters)')
    validate_chinese(line)
    validate_chinese(speaker)
    delivery = ('参考音频：<Audio 1>。' if data.get('audio') else
                '未提供参考音频；由模型原生生成下面指定的中文对白，不模仿未提供的音色。')
    return ('仅生成原生同步现场声音及源剧本规定的中文话语，禁止生成音乐、配乐和乐器声。'
            '禁止计划外台词、未知语言、额外人声、歌声、哼唱和人群喊声；不截断或重复台词。'
            '说话人(S1)：' + speaker.strip() + '；' + delivery + '逐字台词：'
            '<d>[Chinese] ' + line.strip() + '</d>。'
            '按分镜中规定的起止时段与空间透视发声，画外记忆声不得改由画内人物代说。'
            '保持动作声与接触同步，保留自然安静间隙。')



def probe(path, kind):
    if kind == 'image':
        with Image.open(path) as im:
            if im.width * im.height > 4096 * 4096:
                raise ValueError('Image exceeds 16 megapixels; resize before uploading')
            im.verify()
    else:
        with av.open(str(path)) as c:
            streams = [s for s in c.streams if s.type == kind]
            if not streams:
                raise ValueError('File has no ' + kind + ' stream')
            duration = c.duration / av.time_base if c.duration else 0
            if not 0 < duration <= 15:
                raise ValueError('Reference video/audio must have a known duration of at most 15 seconds')
            if kind == 'video' and (duration < 2 or streams[0].width * streams[0].height > 1920 * 1080):
                raise ValueError('Reference video must be 2–15 seconds and at most 1920x1080 pixels')


def inspect_result(path):
    with av.open(str(path)) as c:
        types = {s.type for s in c.streams}
        if not {'video', 'audio'} <= types:
            raise ValueError('Output missing video or native audio stream')
        return {'bytes': path.stat().st_size, 'duration_seconds': c.duration / av.time_base if c.duration else None}


class Gateway:
    def __init__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        self.token_path = STATE / 'token.txt'
        if not self.token_path.exists():
            fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_urlsafe(32) + '\n')
        self.token = self.token_path.read_text().strip()
        self.db = sqlite3.connect(STATE / 'jobs.sqlite3')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY, kind TEXT, path TEXT, name TEXT, sha256 TEXT);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, request TEXT, graph TEXT, state TEXT, created REAL, updated REAL, detail TEXT, result TEXT);''')
        self.db.execute('CREATE TABLE IF NOT EXISTS task_cancellations(id TEXT PRIMARY KEY)')
        self.db.commit()
        self.templates = {p.stem: json.loads(p.read_text()) for p in (ROOT / 'workflows/api').glob('*.json')}
        self.session = None
        self.completion_changed = asyncio.Event()
        self.timings = JobTiming(self.db)
        self.live_progress = LiveProgress(self.timings.event)
        self.browser_tickets = {}
        self.backend_url = BACKEND
        self.pe = PE(self, ROOT, STATE)
        self.backend_down_since = None

    def slots(self, graph):
        return [(k, *TYPES[v['class_type']]) for k, v in graph.items() if v['class_type'] in TYPES]

    def get(self, jid):
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
        if row is None:
            raise web.HTTPNotFound(text='Unknown job')
        return dict(row)

    def update(self, jid, state, detail='', result=None):
        if state in ('queued','submitting','submitted','running'):
            current=self.db.execute('SELECT state FROM jobs WHERE id=?',(jid,)).fetchone()
            if current and current['state']=='interrupted':return
        self.db.execute('UPDATE jobs SET state=?,detail=?,result=?,updated=? WHERE id=?',
                        (state, detail, json.dumps(result) if result else None, time.time(), jid))
        self.db.commit()
        if state in TERMINAL:
            event = self.completion_changed
            self.completion_changed = asyncio.Event()
            event.set()

    async def browser_ticket(self, req):
        now=time.monotonic()
        self.browser_tickets={k:v for k,v in self.browser_tickets.items() if v>now}
        if len(self.browser_tickets)>=100:
            raise web.HTTPTooManyRequests(text='Too many pending browser logins')
        ticket=secrets.token_urlsafe(32)
        self.browser_tickets[ticket]=now+120
        return web.json_response({'ticket':ticket,'expires_in':120},headers={'Cache-Control':'no-store'})

    async def browser_login(self, req):
        data=await req.json()
        ticket=data.get('ticket') if isinstance(data,dict) else None
        if not isinstance(ticket,str) or self.browser_tickets.pop(ticket,0)<=time.monotonic():
            raise web.HTTPUnauthorized(text='Login link expired or already used; launch again')
        return web.json_response({'token':self.token},headers={'Cache-Control':'no-store'})

    async def completion(self, req):
        jid = req.match_info["jid"]
        # Register before reading state: no lost wakeup when a job finishes.
        event = self.completion_changed
        row = self.get(jid)
        deadline = asyncio.get_running_loop().time() + 25
        while row["state"] not in TERMINAL:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(event.wait(), remaining)
            except asyncio.TimeoutError:
                break
            event = self.completion_changed
            row = self.get(jid)
        row = self.get(jid)
        return web.json_response({"completed": row["state"] in TERMINAL, "job": self.public(row)}, headers={"Cache-Control": "no-store"})

    async def comfy(self, path, data=None):
        async with self.session.request('GET' if data is None else 'POST', BACKEND + path, json=data) as r:
            value = await r.json()
            if r.status >= 400:
                raise ValueError(json.dumps(value, ensure_ascii=False)[:4000])
            return value

    async def health(self, req):
        try:
            q = await self.comfy('/queue')
            status = {'available': True, 'running': len(q['queue_running']), 'pending': len(q['queue_pending'])}
        except Exception as exc:
            status = {'available': False, 'error': type(exc).__name__}
        return web.json_response({'service': 'h3-gateway', 'backend': status, 'generation_concurrency': 1, 'architecture': VERSION, 'default_profile': DEFAULT_PROFILE, 'completion_notifications': 'long-poll-v1'})

    async def workflows(self, req):
        return web.json_response({'architecture': VERSION,
            'workflows': [{'name': mode, 'image_count': {'min':reference_limits(mode)[0], 'max':reference_limits(mode)[1]},
                'assets': ([{'kind':k,'min_count':0,'max_count':m} for k,m in [('image',9),('video',3),('audio',3)]]) if mode=='ref_all' else ([{'kind':'image','min_count':1,'max_count':9,'binding':'ref_images.ref_image_{index}'}]+
                           ([{'kind':'audio','required':False}] if mode=='ref_multi' else [{'node':n,'kind':k,'required':True} for n,k,_ in self.slots(self.templates[file]) if k!='image']))
                          if mode in ('ref_multi','ref_media') else [{'node': n, 'kind': k} for n,k,_ in self.slots(self.templates[file])],
                'reference_limits': LIMITS if mode=='ref_all' else None,
                'profiles': [p for p,v in PROFILES.items() if not v.get('fl_only') or mode in ('t2v','i2v','first_last')]}
                for mode,file in MODES.items()],
            'postprocessing': ve.spec(),
            'defaults': {'workflow':'i2v', 'profile':DEFAULT_PROFILE,'width':864,'height':480,'frames':124,'fps':24},
            'sound_policy':SOUND, 'sound_policies':['no_voice','specified_dialogue'],
            'prompt_contract': {'default_format':'auto','format_aliases':['auto','legacy','ref2va_v1'],
                'language':'zh default; explicit en supported','languages':['zh','en'],'fixed_section_order':False,'storyboard_format':'advisory',
                'validation_endpoint':'/v1/validate','dialogue_tags':'auto/legacy normalize; ref2va_v1+specified_dialogue immutable',
                'strict_dialogue_capabilities':dc.CAPABILITIES, 'strict_dialogue_preflight_required':True},
            'legacy_workflow_names':'Accepted as media-mode aliases; profile controls LoRA, sampler and steps.'})

    async def capabilities(self, req):
        return web.json_response({'architecture': VERSION, 'capabilities':dc.CAPABILITIES,
            'details':dc.CAPABILITY_DETAILS}, headers={'Cache-Control':'no-store'})

    async def enhance_info(self, req):
        models = {op: [{'file':f, 'installed':(ROOT/'models'/f).is_file()} for f in ve.MODEL_FILES[op]] for op in ve.spec()['operations']}
        return web.json_response({**ve.spec(), 'version':ve.VERSION, 'models':models,
            'ready':{op:all(x['installed'] for x in items) for op,items in models.items()}}, headers={'Cache-Control':'no-store'})

    async def profiles(self, req):
        return web.json_response({'architecture':VERSION, 'default':DEFAULT_PROFILE,
            'profiles': [{'name':k, **v} for k,v in PROFILES.items()]})

    async def upload(self, req):
        name = unquote(req.headers.get('X-Filename', ''))
        ext = Path(name).suffix.lower()
        kind = next((k for k, extensions in SUFFIX.items() if ext in extensions), None)
        if not kind:
            raise web.HTTPBadRequest(text='Unsupported filename extension; supply URL-encoded X-Filename')
        if req.content_length is not None and req.content_length > MAX_UPLOAD:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=req.content_length)
        aid = str(uuid.uuid4())
        path = ROOT / 'input/gateway' / (aid + ext)
        path.parent.mkdir(parents=True, exist_ok=True)
        size, digest = 0, hashlib.sha256()
        try:
            with path.open('xb') as f:
                async for chunk in req.content.iter_chunked(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=size)
                    digest.update(chunk)
                    f.write(chunk)
            try:
                await asyncio.to_thread(probe, path, kind)
            except Exception as exc:
                raise ValueError('Invalid media: ' + str(exc)) from exc
            self.db.execute('INSERT INTO assets VALUES(?,?,?,?,?)', (aid, kind, path.relative_to(ROOT / 'input').as_posix(), name, digest.hexdigest()))
            self.db.commit()
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return web.json_response({'asset_id': aid, 'kind': kind, 'filename': name, 'bytes': size, 'sha256': digest.hexdigest()}, status=201)

    def build(self, data):
        if ve.applies(data):
            return ve.build(data, self.db, ROOT)
        allowed = {'request_id', 'workflow', 'prompt', 'images', 'video', 'audio', 'width', 'height', 'frames', 'seed', 'profile', 'sound_policy', 'dialogue_text', 'dialogue_speaker', 'prompt_format', 'videos', 'audios', 'dialogue_contract', 'validation_token', 'control_level', 'prompt_language', 'pe_id', 'ui_state'}
        if set(data) - allowed:
            raise ValueError('Unknown fields: ' + ', '.join(sorted(set(data) - allowed)))
        if 'ui_state' in data and (not isinstance(data['ui_state'],dict) or len(json.dumps(data['ui_state']))>100000):
            raise ValueError('Invalid ui_state')
        name = data.get('workflow', '03_i2v_turbo8')
        profile = data.get('profile', DEFAULT_PROFILE)
        graph, mode, trunk = prepare(self.templates, name, profile)
        video_ids = media_ids(data, 'videos', 'video')
        audio_ids = media_ids(data, 'audios', 'audio')
        if mode!='ref_all' and ('videos' in data or 'audios' in data):
            raise ValueError('多个视频/音频请使用 ref_all 模式。')
        sound_data = {**data, 'audio':audio_ids[0] if audio_ids else None}
        prompt = data.get('prompt')
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 16000:
            raise ValueError('prompt must contain 1–16000 characters')
        prompt_format = data.get('prompt_format', 'auto')
        if prompt_format not in ('auto', 'legacy', 'ref2va_v1', 'pe_v1', 'raw_v1'):
            raise ValueError('prompt_format支持auto、legacy、ref2va_v1；省略即可。')
        # Explicit language A/B opt-in. Dialogue validation and asset audits
        # remain unchanged; all existing callers retain the Chinese default.
        prompt_language = data.get('prompt_language', 'zh')
        if prompt_language not in ('zh', 'en'):
            raise ValueError('prompt_language must be zh or en')
        if prompt_language == 'en' and not (prompt_format in ('pe_v1','raw_v1') or dc.strict(data) or
                (prompt_format == 'ref2va_v1' and data.get('sound_policy') == 'no_voice')):
            raise ValueError('English body requires ref2va_v1 and an explicit sound policy')
        # Keep the explicit Ref2VA dialogue control markers intact. The model
        # learned [audio reuse] and <d> as prompt syntax; translating the marker
        # or appending a second sound-policy paragraph can turn that paragraph
        # into generated speech. The auto/legacy adapter remains unchanged.
        policy_text = sound_policy(sound_data)
        images = data.get('images', [])
        if not isinstance(images, list) or not all(isinstance(x, str) for x in images):
            raise ValueError('images must be an ordered list of asset IDs')
        raw_prompt = prompt.strip()
        if prompt_format=='pe_v1':
            pe_row = self.db.execute('SELECT * FROM pe_jobs WHERE id=?',(data.get('pe_id'),)).fetchone()
            if not pe_row or pe_row['state']!='succeeded':
                raise ValueError('PE结果不存在或尚未完成')
            pe_request=json.loads(pe_row['request']);pe_result=json.loads(pe_row['result'])
            for key in ('images','frames','width','height','workflow','sound_policy','dialogue_text','dialogue_speaker','dialogue_contract','control_level'):
                if data.get(key, [] if key=='images' else None)!=pe_request.get(key, [] if key=='images' else None):
                    raise ValueError('生成设置已改变，请重新进行PE改写：'+key)
            if video_ids!=pe_request.get('videos',[]) or audio_ids!=pe_request.get('audios',[]):
                raise ValueError('参考素材已改变，请重新改写')
            if raw_prompt!=pe_result['prompt'] or prompt_language!=pe_result['language']:
                raise ValueError('PE正文或语言已改变，请重新改写')
            prompt=validate_pe_result(pe_result['sections'],pe_request)
            if dc.strict(data):
                dc.prompt_check(data)
            prompt_warnings=['本地PE格式校验通过，声音与画面仍须实际验收。']
        elif dc.strict(data):
            prompt = dc.prompt_check(data)
            if prompt_language == 'zh' and prompt_format!='raw_v1':
                validate_chinese(prompt)
            prompt_warnings = []
        elif prompt_language == 'en' or prompt_format=='raw_v1':
            # English silent-scene passthrough: preserve native physical sound,
            # reject speech tags and invalid references, do not append prose.
            if '<d>' in raw_prompt or '</d>' in raw_prompt or '[audio reuse]' in raw_prompt:
                raise ValueError('no_voice must not contain dialogue or audio reuse')
            for kind, count in [('Picture', len(images)), ('Video', len(video_ids)), ('Audio', len(audio_ids))]:
                indices = [int(n) for n in re.findall(r'<'+kind+r'\s+(\d+)>', raw_prompt, re.I)]
                if any(n < 1 or n > count for n in indices):
                    raise ValueError('Reference index out of range: ' + kind)
            prompt, prompt_warnings = raw_prompt, []
        else:
            normalized_prompt, prompt_warnings = normalize_prompt(sound_data, len(images), len(video_ids), len(audio_ids))
            if '<d>' in normalized_prompt:
                policy_text = re.sub(r'<d>.*?</d>', '见分镜内已标注的中文台词', policy_text, flags=re.S)
            prompt = normalized_prompt + '\n\n' + policy_text
        width, height, frames, seed = [data.get(k, v) for k, v in [('width', 864), ('height', 480), ('frames', 124), ('seed', 7)]]
        if any(type(x) is not int for x in (width, height, frames, seed)):
            raise ValueError('Dimensions, frames and seed must be integers')
        if min(width, height) < 256 or max(width, height) > 1344 or width % 32 or height % 32:
            raise ValueError('Dimensions must be multiples of 32, between 256 and 1344')
        if not 5 <= frames <= 3600 or (frames - 5) % 17:
            raise ValueError('frames must follow the model node: 5+17*n, at most 3600; no pixel-frame budget')
        if not 0 <= seed < 2**64:
            raise ValueError('seed must be an unsigned 64-bit integer')
        images = data.get('images', [])
        if not isinstance(images, list) or not all(isinstance(x, str) for x in images):
            raise ValueError('images must be an ordered list of asset IDs')
        graph = expand(graph, images, video_ids, audio_ids) if mode=='ref_all' else configure_references(graph, mode, len(images), bool(data.get('audio')))
        slots = self.slots(graph)
        if len(images) != sum(k == 'image' for _, k, _ in slots):
            raise ValueError('Wrong image count for this workflow; see /v1/workflows')
        for kind in ('video', 'audio'):
            present = bool(video_ids if kind=='video' else audio_ids)
            if present != any(k == kind for _, k, _ in slots):
                raise ValueError('Unexpected or missing ' + kind + ' asset')
        image_iter = iter(images)
        media_iters = {'video':iter(video_ids),'audio':iter(audio_ids)}
        for node, kind, field in slots:
            aid = next(image_iter) if kind == 'image' else next(media_iters[kind])
            if not isinstance(aid, str):
                raise ValueError('Asset IDs must be strings')
            row = self.db.execute('SELECT * FROM assets WHERE id=?', (aid,)).fetchone()
            if not row or row['kind'] != kind:
                raise ValueError('Unknown asset or wrong type: ' + aid)
            graph[node]['inputs'][field] = row['path']
        reference_seconds = check_durations(graph, ROOT) if mode=='ref_all' else None
        graph['7']['inputs'].update(prompt=prompt, width=width, height=height, length=frames)
        graph['10']['inputs']['noise_seed'] = seed
        graph = finish(graph, profile, mode, trunk)
        graph['15']['_meta']['gateway_config']['reference_images'] = len(images)
        graph['15']['_meta']['gateway_config']['prompt_language'] = prompt_language
        graph['15']['_meta']['gateway_config']['reference_audio'] = bool(audio_ids)
        graph['15']['_meta']['gateway_config']['reference_videos'] = len(video_ids)
        graph['15']['_meta']['gateway_config']['reference_audios'] = len(audio_ids)
        graph['15']['_meta']['gateway_config']['reference_seconds'] = reference_seconds
        graph['15']['_meta']['gateway_config']['prompt_warnings'] = prompt_warnings
        if prompt_format=='raw_v1':
            graph['7']['inputs']['prompt']=raw_prompt
        if dc.strict(data):
            try:
                jid = str(uuid.UUID(data['request_id']))
            except (KeyError, ValueError, TypeError, AttributeError):
                raise ValueError('指定对白预检须提供request_id UUID，并在提交时保持相同。')
            graph['15']['inputs']['filename_prefix'] = 'H3/gateway/' + jid
            # LoRA triggers must not rewrite this explicit prompt path.
            graph['7']['inputs']['prompt'] = prompt
            temporal_guides.wire(graph, data)
            audit = dc.audit(graph, data, self.db, ROOT, self.slots(graph))
            graph['15']['_meta']['gateway_config']['dialogue_audit'] = audit
        return graph

    async def prepare_reference_videos(self, data):
        from reference_video import normalize
        for aid in media_ids(data,'videos','video'):
            asset=self.db.execute('SELECT path,kind FROM assets WHERE id=?',(aid,)).fetchone()
            if not asset or asset['kind']!='video':raise ValueError('参考视频不存在')
            await asyncio.to_thread(normalize,ROOT/'input'/asset['path'],ROOT)

    async def validate_request(self, req):
        data = await req.json()
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object')
        await self.prepare_reference_videos(data)
        try:
            graph = self.build(data)
        except ValueError as exc:
            if dc.strict(data):
                return web.json_response({'valid':False,'submitted':False,'submission_allowed':False,
                    'error':str(exc),'capabilities':dc.CAPABILITIES,'capability_details':dc.CAPABILITY_DETAILS}, status=400)
            raise
        config = graph['15']['_meta']['gateway_config']
        audit = config.get('dialogue_audit') or config.get('enhance_audit')
        return web.json_response({'valid': True, 'submitted': False,
            'config': config, 'effective_prompt': config.get('effective_prompt','') if ve.applies(data) else graph['7']['inputs']['prompt'],
            'submission_allowed': audit['submission_allowed'] if audit else True,
            **({k:audit[k] for k in ('control_level','input_integrity','unverified_risks')} if audit else {}),
            'audit': audit,
            'validation_token': dc.make_token(audit, self.token) if audit else None})

    async def cancel(self, req):
        jid=str(uuid.UUID(req.match_info['jid']))
        video=self.db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        pe=self.db.execute('SELECT * FROM pe_jobs WHERE id=?',(jid,)).fetchone()
        if not video and not pe:raise web.HTTPNotFound(text='任务不存在')
        if video and video['state'] in TERMINAL:return web.json_response({'status':video['state'],'cancelled':False})
        if not video and pe['state'] in TERMINAL:
            follow=self.pe.continuations.status(jid)
            if not follow or follow['status']!='waiting':return web.json_response({'status':pe['state'],'cancelled':False})
        self.db.execute('INSERT OR IGNORE INTO task_cancellations VALUES(?)',(jid,))
        self.db.execute("UPDATE pe_followups SET state='cancelled',error='用户取消任务' WHERE pe_id=? AND state='waiting'",(jid,));self.db.commit()
        if video:
            if video['state']!='queued':
                result=await self.comfy('/api/jobs/'+jid+'/cancel',{})
                if not result.get('cancelled'):
                    await self.reconcile(dict(video))
                    return web.json_response({'status':self.get(jid)['state'],'cancelled':False})
            self.update(jid,'interrupted','用户中断任务')
        else:
            if self.pe.executing_id==jid and self.pe.execution:
                self.pe.execution.cancel()
                with contextlib.suppress(asyncio.CancelledError):await self.pe.execution
            self.pe.update(jid,'interrupted',error='用户取消任务，未继续提交视频')
        return web.json_response({'status':'interrupted','cancelled':True})

    async def submit(self, req):
        data = await req.json()
        if not isinstance(data, dict):
            raise ValueError('Expected a JSON object')
        raw_id = data.get('request_id')
        if not isinstance(raw_id, str):
            raise ValueError('request_id must be a UUID string')
        jid = str(uuid.UUID(raw_id))
        encoded = json.dumps(data, sort_keys=True, ensure_ascii=False)
        existing = self.db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
        if existing:
            if existing['request'] != encoded:
                raise web.HTTPConflict(text='request_id already used for different parameters')
            return web.json_response(self.public(dict(existing)))
        await self.prepare_reference_videos(data)
        if self.db.execute('SELECT id FROM task_cancellations WHERE id=?',(jid,)).fetchone():raise ValueError('任务已取消')
        graph = self.build(data)
        graph['15']['inputs']['filename_prefix'] = 'H3/gateway/' + jid
        if ve.applies(data):
            audit = graph['15']['_meta']['gateway_config']['enhance_audit']
            if not audit['submission_allowed']:
                raise ValueError('后处理模型未就绪：'+', '.join(graph['15']['_meta']['gateway_config']['missing_models']))
            dc.verify_token(data.get('validation_token'), audit, self.token)
        if dc.strict(data):
            audit = graph['15']['_meta']['gateway_config']['dialogue_audit']
            if not audit['submission_allowed']:
                raise ValueError('当前无法按要求强制控制，禁止排队：' + '；'.join(audit['unsupported_reasons']))
            dc.verify_token(data.get('validation_token'), audit, self.token)
            logging.info('Dialogue contract job=%s prompt_sha256=%s workflow_sha256=%s',jid,audit['prompt_sha256'],audit['workflow_sha256'])
        active = self.db.execute("SELECT COUNT(*) FROM jobs WHERE state NOT IN ('succeeded','failed','interrupted')").fetchone()[0]
        if active >= 100:
            raise web.HTTPTooManyRequests(text='Queue has 100 unfinished jobs')
        now = time.time()
        self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)', (jid, encoded, json.dumps(graph), 'queued', now, now, '', None))
        self.db.commit()
        return web.json_response(self.public(self.get(jid)), status=202)

    def public(self, row):
        result = json.loads(row['result']) if row['result'] else None
        saved_request = json.loads(row['request'])
        config = json.loads(row['graph']).get('15',{}).get('_meta',{}).get('gateway_config',{})
        audit = config.get('dialogue_audit') or config.get('enhance_audit')
        return {**({k:audit[k] for k in ('submission_allowed','control_level','input_integrity','unverified_risks') if k in audit} if audit else {}), 'request': {k:v for k,v in saved_request.items() if k!='validation_token'} if dc.strict(saved_request) or ve.applies(saved_request) else None, 'job_id': row['id'], 'status': row['state'], 'created': row['created'], 'updated': row['updated'],
                'detail': row['detail'], 'result': result,
                'timing': self.timings.describe(row),
                'progress': self.live_progress.for_job(row['id'], json.loads(row['graph'])) if row['state']=='running' else None,
                'config': json.loads(row['graph']).get('15', {}).get('_meta', {}).get('gateway_config', {'architecture':'legacy'}),
                'download_url': '/v1/jobs/' + row['id'] + '/video' if row['state'] == 'succeeded' else None,
                'review': 'Generation status is technical only; narrative and sound require human review.'}

    def asset_info(self, aid):
        row=self.db.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
        if not row:return {'asset_id':aid,'missing':True}
        path=(ROOT/'input'/row['path']).resolve()
        available=path.is_relative_to((ROOT/'input').resolve()) and path.is_file()
        return {'asset_id':aid,'kind':row['kind'],'filename':row['name'],'sha256':row['sha256'],'bytes':path.stat().st_size if available else 0,'missing':not available}

    async def asset_content(self, req):
        aid=req.match_info['aid'];info=self.asset_info(aid)
        if info.get('missing'):raise web.HTTPNotFound(text='素材原文件已不存在')
        row=self.db.execute('SELECT path FROM assets WHERE id=?',(aid,)).fetchone()
        return web.FileResponse((ROOT/'input'/row['path']).resolve(),headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    async def job_detail(self, req):
        jid=req.match_info['jid']
        row=self.db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        if row:
            d=json.loads(row['request']);g=json.loads(row['graph']);cfg=g.get('15',{}).get('_meta',{}).get('gateway_config',{})
        else:
            row=self.db.execute('SELECT request FROM pe_followups WHERE pe_id=?',(jid,)).fetchone()
            if not row:raise web.HTTPNotFound()
            d=json.loads(row['request']);g={};cfg={'stage':'PE自动任务'}
            d['pe_id']=jid
        d.pop('validation_token',None)
        assets=[]
        for kind,ids in [('image',d.get('images',[])),('video',media_ids(d,'videos','video')),('audio',media_ids(d,'audios','audio'))]:
            for i,aid in enumerate(ids):assets.append({**self.asset_info(aid),'kind':kind,'label':{'image':'Picture','video':'Video','audio':'Audio'}[kind]+' '+str(i+1)})
        pe=None
        if d.get('pe_id'):
            r=self.db.execute('SELECT request,result FROM pe_jobs WHERE id=?',(d['pe_id'],)).fetchone()
            if r:pe={'request':json.loads(r['request']),'result':json.loads(r['result']) if r['result'] else None}
        return web.json_response({'request':d,'assets':assets,'config':cfg,'effective_prompt':g.get('7',{}).get('inputs',{}).get('prompt',d.get('prompt','')),'pe':pe})

    async def status(self, req):
        row = self.get(req.match_info['jid'])
        value = self.public(row)
        try:
            q = await self.comfy('/queue')
            ids = [x[1] for x in q['queue_pending']]
            value['backend_pending_position'] = ids.index(row['id']) + 1 if row['id'] in ids else None
            value['backend_running_jobs'] = len(q['queue_running'])
        except Exception:
            value['backend_available'] = False
        return web.json_response(value)

    async def jobs(self, req):
        return web.json_response({'jobs': [self.public(dict(r)) for r in self.db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 100')]})

    async def dashboard(self, req):
        import re
        limit = max(1, min(int(req.query.get('limit', '100')), 10000))
        rows = self.db.execute("SELECT * FROM jobs ORDER BY CASE WHEN state IN ('succeeded','failed','interrupted') THEN 1 ELSE 0 END, created DESC LIMIT ?", (limit,)).fetchall()
        waiting = [r[0] for r in self.db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created")]
        counts = dict(self.db.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state').fetchall())
        backend = {'available':False}
        pending = []
        try:
            q = await asyncio.wait_for(self.comfy('/queue'), 4)
            pending = [x[1] for x in q['queue_pending']]
            backend = {'available':True, 'running':len(q['queue_running']), 'pending':len(pending)}
        except Exception:
            pass
        jobs = []
        for r in rows:
            row = dict(r); value = self.public(row)
            graph = json.loads(row['graph']); inputs = graph.get('7',{}).get('inputs',{})
            prompt = inputs.get('prompt','')
            title = re.search(r'【镜头[^】]*】',prompt)
            value.update(title=title.group(0) if title else '任务 '+row['id'][:8], width=inputs.get('width'), height=inputs.get('height'), frames=inputs.get('length'),
                backend_pending_position=pending.index(row['id'])+1 if row['id'] in pending else None,
                gateway_pending_position=waiting.index(row['id'])+1 if row['id'] in waiting else None)
            jobs.append(value)
        pipelines = self.pe.continuations.cards()
        for item in pipelines:
            key = 'running' if item['status']=='pe_running' else 'queued' if item['status']=='pe_queued' else 'failed'
            counts[key] = counts.get(key,0)+1
        jobs = sorted(jobs+pipelines,key=lambda x:(x['status'] in TERMINAL,-x['created']))[:limit]
        return web.json_response({'jobs':jobs, 'backend':backend, 'has_more':sum(counts.values())>len(jobs),
            'counts':{'queued':sum(counts.get(k,0) for k in ('queued','submitting','submitted')), 'running':counts.get('running',0),
                      'succeeded':counts.get('succeeded',0), 'failed':counts.get('failed',0)+counts.get('interrupted',0)}}, headers={'Cache-Control':'no-store'})

    async def download(self, req):
        row = self.get(req.match_info['jid'])
        if row['state'] != 'succeeded':
            raise web.HTTPConflict(text='Video is not ready')
        result = json.loads(row['result'])
        path = (ROOT / 'output' / result['file']).resolve()
        if not path.is_relative_to((ROOT / 'output').resolve()) or not path.is_file():
            raise web.HTTPNotFound(text='Output missing')
        return web.FileResponse(path, headers={'Content-Disposition': 'attachment; filename="' + row['id'] + '.mp4"'})

    async def reconcile(self, row):
        jid = row['id']
        history = await self.comfy('/history/' + jid)
        q = await self.comfy('/queue')
        if jid not in history and any(x[1] == jid for x in q['queue_running'] + q['queue_pending']):
            state = 'running' if any(x[1] == jid for x in q['queue_running']) else 'submitted'
            if state != row['state']:
                self.update(jid, state)
            return
        # Recheck history after queue to cover the completion boundary.
        if jid not in history:
            history = await self.comfy('/history/' + jid)
        if jid in history:
            item = history[jid]
            self.timings.history(item)
            if item['status']['status_str'] != 'success':
                self.update(jid, 'failed', json.dumps(item['status'], ensure_ascii=False)[:4000])
                return
            files = [v for out in item.get('outputs', {}).values() for values in out.values() if isinstance(values, list)
                     for v in values if isinstance(v, dict) and str(v.get('filename', '')).endswith('.mp4')]
            if not files:
                self.update(jid, 'failed', 'ComfyUI completed without an MP4')
                return
            v = files[0]
            path = (ROOT / 'output' / v.get('subfolder', '') / v['filename']).resolve()
            if not path.is_relative_to((ROOT / 'output/H3/gateway').resolve()) or not path.name.startswith(jid):
                self.update(jid, 'failed', 'Unexpected output path')
                return
            graph = json.loads(row['graph'])
            if ve.applies(json.loads(row['request'])):
                result = await asyncio.to_thread(ve.inspect_and_preserve, path, graph, ROOT)
            else:
                result = await asyncio.to_thread(inspect_result, path)
                result['audio_source'] = 'H3 native synchronized audio'
            result.update(file=path.relative_to(ROOT / 'output').as_posix())
            self.update(jid, 'succeeded', result=result)
        elif time.time() - row['updated'] > 120:
            self.update(jid, 'interrupted', 'No backend queue/history record. Backend may have restarted or history was cleared. Not automatically resubmitted; use a new request_id to retry after checking outputs.')

    async def worker(self):
        while True:
            row = self.db.execute("SELECT * FROM jobs WHERE state NOT IN ('succeeded','failed','interrupted') ORDER BY created LIMIT 1").fetchone()
            if self.pe.process is not None or self.pe.precedes(row):
                await asyncio.sleep(1)
                continue
            if row:
                row = dict(row)
                try:
                    if row['state'] == 'queued':
                        await self.comfy('/queue')  # Remain durably queued if backend is offline.
                        if self.db.execute('SELECT id FROM task_cancellations WHERE id=?',(row['id'],)).fetchone():
                            self.update(row['id'],'interrupted','用户取消任务');continue
                        self.update(row['id'], 'submitting')
                        try:
                            await self.comfy('/prompt', {'prompt_id': row['id'], 'client_id': 'h3-gateway', 'prompt': json.loads(row['graph'])})
                        except ValueError as exc:
                            self.update(row['id'], 'failed', str(exc))
                        else:
                            if self.db.execute('SELECT id FROM task_cancellations WHERE id=?',(row['id'],)).fetchone():
                                await self.comfy('/api/jobs/'+row['id']+'/cancel',{})
                                self.update(row['id'],'interrupted','用户取消任务')
                            else:self.update(row['id'], 'submitted')
                    else:
                        await self.reconcile(row)
                    self.backend_down_since = None
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    now = time.monotonic()
                    if self.backend_down_since is None:
                        self.backend_down_since = now
                    elif row['state'] != 'queued' and now - self.backend_down_since > 120:
                        self.update(row['id'], 'interrupted', 'Backend unavailable for over 120 seconds; execution cannot be confirmed. Check outputs before retrying with a new request_id.')
                except Exception as exc:
                    logging.exception('Job monitor error')
                    self.update(row['id'], 'failed', str(exc)[:2000])
            await asyncio.sleep(2)

    async def lifecycle(self, app):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120, connect=5)) as self.session:
            task = asyncio.create_task(self.worker())
            pe_task = asyncio.create_task(self.pe.run())
            progress_task = asyncio.create_task(self.live_progress.run(self.session, BACKEND))
            yield
            pe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pe_task
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.db.close()


def create_app():
    gateway = Gateway()

    @web.middleware
    async def guard(req, handler):
        if req.method in ('GET','HEAD') and req.path in ('/','/dashboard.js'):
            return await handler(req)
        if req.method=='POST' and req.path=='/v1/browser-login':
            try:
                return await handler(req)
            except web.HTTPException as exc:
                return web.json_response({'error':exc.text},status=exc.status)
        supplied = req.headers.get('Authorization', '')
        if not hmac.compare_digest(supplied.encode('utf-8'), ('Bearer ' + gateway.token).encode('utf-8')):
            return web.json_response({'error': 'Unauthorized'}, status=401)
        try:
            if req.method=='POST' and req.path in ('/v1/jobs','/v1/pe'):
                bootstrap_status.ready()
            return await handler(req)
        except web.HTTPException as exc:
            return web.json_response({'error': exc.text}, status=exc.status)
        except (ValueError, TypeError, KeyError) as exc:
            return web.json_response({'error': str(exc)}, status=400)
        except Exception:
            logging.exception('Request failed')
            return web.json_response({'error': 'Internal error; see server log'}, status=500)

    async def dashboard_asset(req):
        path = Path(__file__).with_name('dashboard.html' if req.path == '/' else 'dashboard.js')
        return web.FileResponse(path, headers={'Cache-Control':'no-store', 'Content-Security-Policy':
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})

    app = web.Application(middlewares=[guard], client_max_size=1024**2)
    app.cleanup_ctx.append(gateway.lifecycle)
    app.add_routes([web.get('/v1/bootstrap', bootstrap_status.info), web.post('/v1/bootstrap/retry', bootstrap_status.retry), web.get('/', dashboard_asset), web.get('/dashboard.js', dashboard_asset), web.get('/v1/dashboard', gateway.dashboard), web.get('/v1/health', gateway.health), web.get('/v1/workflows', gateway.workflows), web.get('/v1/video-enhance', gateway.enhance_info), web.get('/v1/profiles', gateway.profiles), web.get('/v1/capabilities', gateway.capabilities),
                    web.get('/v1/assets/{aid}/content', gateway.asset_content), web.get('/v1/jobs/{jid}/detail', gateway.job_detail), web.post('/v1/jobs/{jid}/cancel', gateway.cancel), web.get('/v1/pe', gateway.pe.info), web.post('/v1/pe', gateway.pe.submit), web.get('/v1/pe/{pid}', gateway.pe.status), web.post('/v1/browser-ticket', gateway.browser_ticket), web.post('/v1/browser-login', gateway.browser_login), web.post('/v1/assets', gateway.upload), web.post('/v1/jobs', gateway.submit), web.post('/v1/validate', gateway.validate_request),
                    web.get('/v1/jobs', gateway.jobs), web.get('/v1/jobs/{jid}', gateway.status),
                    web.get('/v1/jobs/{jid}/completion', gateway.completion), web.get('/v1/jobs/{jid}/video', gateway.download)])
    return app


if __name__ == '__main__':
    import fcntl
    STATE.mkdir(parents=True, exist_ok=True)
    lock = (STATE / 'server.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), host='127.0.0.1', port=int(os.environ.get('H3_GATEWAY_PORT', '8190')), access_log=None)
