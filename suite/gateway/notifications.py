"""Per-receipt completion subscriptions; no inbound port needed on the client."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def notify(job):
    success = job['status'] == 'succeeded'
    title = 'H3 视频生成完成' if success else 'H3 视频任务未完成'
    message = ('视频已生成，可以下载。' if success else '任务失败或中断，请查看任务状态与日志。')
    message += '\n任务编号：' + job['job_id']
    if os.name == 'nt':
        import ctypes
        # Fixed local text only; never interpolate remote text into PowerShell/code.
        shown = ctypes.windll.user32.MessageBoxW(None, message, title, 0x40 | 0x10000)
        if not shown:
            raise OSError('Windows notification could not be displayed')
    else:
        print(title + '\n' + message, flush=True)


@contextlib.contextmanager
def receipt_lock(path):
    with path.open('a+b') as f:
        if path.stat().st_size == 0:
            f.write(b'0'); f.flush()
        f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def watch(client, receipt):
    receipt = Path(receipt).resolve()
    saved = json.loads(receipt.read_text(encoding='utf-8'))
    jid = str(uuid.UUID(saved['request']['request_id']))
    marker = receipt.with_name(receipt.name + '.notification.json')
    with receipt_lock(receipt.with_name(receipt.name + '.notification.lock')) as acquired:
        if not acquired:
            return
        if marker.exists():
            previous = json.loads(marker.read_text(encoding='utf-8'))
            if previous.get('job_id') == jid and previous.get('url') == saved['url']:
                return
        delay = 2
        while True:
            try:
                event = client.request('GET', '/v1/jobs/' + jid + '/completion')
                delay = 2
            except Exception as exc:
                if isinstance(exc, RuntimeError) and any(str(exc).startswith('HTTP ' + code) for code in ('400','401','403','404')):
                    raise
                print('通知连接暂时中断，稍后重连：' + str(exc), flush=True)
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            if not event['completed']:
                continue
            job = event['job']
            if job['job_id'] != jid or job['status'] not in ('succeeded','failed','interrupted'):
                raise RuntimeError('Unexpected completion response')
            notify(job)
            tmp = marker.with_name(marker.name + '.tmp')
            tmp.write_text(json.dumps({'job_id':jid,'url':saved['url'],'status':job['status'],
                'notified_at':time.time()}, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(marker)
            return


def start_watcher(script, receipt, url, token_file):
    receipt = Path(receipt).resolve()
    args = [sys.executable, str(Path(script).resolve()), '--url', url, '--token-file',
            str(Path(token_file).resolve()), 'watch', str(receipt)]
    kwargs = {'stdin':subprocess.DEVNULL, 'close_fds':True}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    log = receipt.with_name(receipt.name + '.notification.log')
    with log.open('ab') as f:
        proc = subprocess.Popen(args, stdout=f, stderr=f, **kwargs)
    return {'notification_pid':proc.pid, 'notification_log':str(log)}
