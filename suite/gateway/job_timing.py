"""Persist ComfyUI execution timestamps separately from gateway queue timestamps."""
import math
import time

TERMINAL=('succeeded','failed','interrupted')
class JobTiming:
    def __init__(self, db):
        self.db=db
        db.execute('CREATE TABLE IF NOT EXISTS job_timings(id TEXT PRIMARY KEY, started REAL, finished REAL)')
        db.commit()

    def event(self, kind, data):
        if kind not in ('execution_start','execution_success','execution_error','execution_interrupted'):return
        jid,stamp=data.get('prompt_id'),data.get('timestamp')
        if not isinstance(jid,str) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp):return
        stamp/=1000
        row=self.db.execute('SELECT created FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row or stamp<row[0]-1 or stamp>time.time()+60:return
        field='started' if kind=='execution_start' else 'finished'
        self.db.execute(f'INSERT INTO job_timings(id,{field}) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET {field}=COALESCE(job_timings.{field},excluded.{field})',(jid,stamp))
        self.db.commit()

    def history(self, obj):
        if isinstance(obj,dict):
            messages=obj.get('messages')
            if isinstance(messages,list):
                for item in messages:
                    if isinstance(item,list) and len(item)==2 and isinstance(item[1],dict):self.event(item[0],item[1])
            for k,v in obj.items():
                if k!='messages' and isinstance(v,(dict,list)):self.history(v)
        elif isinstance(obj,list):
            for v in obj:
                if isinstance(v,(dict,list)):self.history(v)

    def describe(self, row, now=None):
        now=time.time() if now is None else now
        t=self.db.execute('SELECT started,finished FROM job_timings WHERE id=?',(row['id'],)).fetchone()
        start,end=t if t else (None,None)
        done=row['state'] in TERMINAL
        total=max(0,(row['updated'] if done else now)-row['created'])
        valid=start is not None and (end is None or end>=start)
        generation=end-start if valid and end is not None else None
        elapsed=max(0,now-start) if valid and not done and end is None else None
        return {'generation_seconds':round(generation,1) if generation is not None else None,
                'running_seconds':round(elapsed,1) if elapsed is not None else None,
                'queue_seconds':round(max(0,start-row['created']),1) if valid else None,
                'total_seconds':round(total,1), 'started':start,'finished':end,
                'source':'comfyui_execution_events' if generation is not None or elapsed is not None else 'gateway_total_only'}
