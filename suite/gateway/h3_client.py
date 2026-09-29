"""Windows/Linux Python 3.9+ client. No third-party dependencies."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit, quote


class Client:
    def __init__(self, url, token):
        self.url = urlsplit(url)
        if self.url.scheme not in ('http', 'https') or self.url.path not in ('', '/'):
            raise ValueError('URL must be http(s)://host:port without a path')
        self.token = token.strip()

    def request(self, method, path, data=None, upload=None, download=None):
        cls = http.client.HTTPSConnection if self.url.scheme == 'https' else http.client.HTTPConnection
        conn = cls(self.url.hostname, self.url.port, timeout=180)
        headers = {'Authorization': 'Bearer ' + self.token}
        handle = None
        try:
            if upload:
                handle = Path(upload).open('rb')
                data = handle
                headers.update({'Content-Length': str(Path(upload).stat().st_size), 'Content-Type': 'application/octet-stream', 'X-Filename': quote(Path(upload).name)})
            elif data is not None:
                data = json.dumps(data, ensure_ascii=False).encode('utf-8')
                headers['Content-Type'] = 'application/json'
            conn.request(method, path, body=data, headers=headers)
            res = conn.getresponse()
            if res.status >= 400:
                raise RuntimeError(f'HTTP {res.status}: {res.read().decode("utf-8", errors="replace")}')
            if download:
                target = Path(download)
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_name(target.name + '.part')
                received = 0
                with partial.open('wb') as out:
                    while True:
                        chunk = res.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                        received += len(chunk)
                if res.getheader('Content-Length') is not None and received != int(res.getheader('Content-Length')):
                    raise RuntimeError('Incomplete download; retry the download command')
                partial.replace(target)
                return {'saved': str(target.resolve()), 'bytes': received}
            return json.loads(res.read())
        finally:
            if handle:
                handle.close()
            conn.close()

    def upload(self, path):
        result = self.request('POST', '/v1/assets', upload=path)
        digest = hashlib.sha256()
        with Path(path).open('rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != result['sha256']:
            raise RuntimeError('Upload checksum mismatch')
        return result['asset_id']


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('H3_URL', 'http://127.0.0.1:8190'))
    parser.add_argument('--token-file', default=os.environ.get('H3_TOKEN_FILE', str(Path(__file__).with_name('token.txt'))))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('health')
    sub.add_parser('workflows')
    sub.add_parser('profiles')
    sub.add_parser('jobs')
    submit = sub.add_parser('submit')
    submit.add_argument('--workflow', default='i2v')
    submit.add_argument('--profile', default='physics', help='LoRA preset; list with profiles')
    submit.add_argument('--sound-policy', choices=['no_voice','specified_dialogue'], default='no_voice')
    submit.add_argument('--dialogue-text')
    submit.add_argument('--dialogue-speaker')
    submit.add_argument('--prompt-file', required=True, help='UTF-8 prompt text file')
    submit.add_argument('--image', action='append', default=[], help='Repeat in reference order; ref_multi/ref_media accept 1–9 images')
    submit.add_argument('--video', action='append', default=[])
    submit.add_argument('--audio', action='append', default=[])
    submit.add_argument('--width', type=int, default=864)
    submit.add_argument('--height', type=int, default=480)
    submit.add_argument('--frames', type=int, default=124)
    submit.add_argument('--seed', type=int, default=7)
    submit.add_argument('--receipt', required=True, help='New receipt JSON path; use resume if it already exists')
    submit.add_argument('--wait', action='store_true')
    submit.add_argument('--notify', action=argparse.BooleanOptionalAction, default=True, help='Background completion notification (default on)')
    submit.add_argument('--out', default='h3_output')
    for name in ('status', 'wait', 'download'):
        cmd = sub.add_parser(name)
        cmd.add_argument('job_id')
        if name != 'status':
            cmd.add_argument('--out', default='h3_output')
    resume = sub.add_parser('resume')
    resume.add_argument('receipt')
    resume.add_argument('--out', default='h3_output')
    resume.add_argument('--notify', action=argparse.BooleanOptionalAction, default=True)
    watcher = sub.add_parser('watch')
    watcher.add_argument('receipt')
    args = parser.parse_args()
    c = Client(args.url, Path(args.token_file).read_text(encoding='utf-8-sig'))
    if args.command == 'watch':
        from notifications import watch
        saved = json.loads(Path(args.receipt).read_text(encoding='utf-8'))
        if saved['url'].rstrip('/') != args.url.rstrip('/'):
            raise ValueError('Receipt URL differs; use its server URL')
        watch(c, args.receipt)
        return
    if args.command in ('health', 'workflows', 'profiles', 'jobs'):
        emit(c.request('GET', '/v1/' + args.command))
        return
    if args.command == 'submit':
        receipt = Path(args.receipt)
        if receipt.exists():
            raise RuntimeError('Receipt already exists. Use resume to avoid duplicate generation.')
        prompt = Path(args.prompt_file).read_text(encoding='utf-8-sig')
        data = {'request_id': str(uuid.uuid4()), 'workflow': args.workflow, 'profile':args.profile, 'sound_policy':args.sound_policy, 'prompt': prompt,
                'width': args.width, 'height': args.height, 'frames': args.frames, 'seed': args.seed,
                'images': [c.upload(p) for p in args.image]}
        for field in ('dialogue_text','dialogue_speaker'):
            if getattr(args, field) is not None:
                data[field] = getattr(args, field)
        for kind in ('video', 'audio'):
            if getattr(args, kind):
                files=getattr(args,kind)
                if args.workflow=='ref_all':
                    data[kind+'s']=[c.upload(f) for f in files]
                elif len(files)==1:
                    data[kind]=c.upload(files[0])
                else:
                    raise ValueError('多个视频/音频请使用 ref_all 模式。')
        # Save the exact request BEFORE submitting. Resume repeats the same UUID/body safely.
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with receipt.open('x', encoding='utf-8') as f:
            json.dump({'url': args.url, 'request': data}, f, ensure_ascii=False, indent=2)
        result = c.request('POST', '/v1/jobs', data=data)
        emit(result)
        if args.notify:
            from notifications import start_watcher
            emit(start_watcher(__file__, args.receipt, args.url, args.token_file))
        if not args.wait:
            return
        jid = result['job_id']
    elif args.command == 'resume':
        saved = json.loads(Path(args.receipt).read_text(encoding='utf-8'))
        if saved['url'].rstrip('/') != args.url.rstrip('/'):
            raise ValueError('Receipt URL differs; connect to its server URL explicitly')
        result = c.request('POST', '/v1/jobs', data=saved['request'])
        jid = result['job_id']
        if args.notify:
            from notifications import start_watcher
            emit(start_watcher(__file__, args.receipt, args.url, args.token_file))
    else:
        jid = str(uuid.UUID(args.job_id))
    if args.command == 'status':
        emit(c.request('GET', '/v1/jobs/' + jid))
        return
    if args.command != 'download':
        last = None
        while True:
            state = c.request('GET', '/v1/jobs/' + jid)
            marker = (state['status'], state.get('backend_pending_position'))
            if marker != last:
                emit(state)
                last = marker
            if state['status'] == 'succeeded':
                break
            if state['status'] in ('failed', 'interrupted'):
                raise RuntimeError(state['detail'])
            time.sleep(5)
    emit(c.request('GET', '/v1/jobs/' + jid + '/video', download=Path(args.out) / (jid + '.mp4')))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit('Stopped waiting. Server task continues; use resume or wait later.')
    except Exception as exc:
        raise SystemExit(str(exc))
