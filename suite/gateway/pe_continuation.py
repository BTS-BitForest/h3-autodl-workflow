"""Durable PE-to-video continuation, reusing normal validation and submission."""
import json
from aiohttp import web
class Request:
 def __init__(self,data):self.data=data
 async def json(self):return self.data
class Continuations:
 def __init__(self,gateway):
  self.g=gateway;self.db=gateway.db
  self.db.execute('CREATE TABLE IF NOT EXISTS pe_followups(pe_id TEXT PRIMARY KEY,request TEXT,state TEXT,error TEXT)')
  self.db.commit()
 def add(self,pid,data):
  self.db.execute('INSERT INTO pe_followups VALUES(?,?,?,?)',(pid,json.dumps(data,ensure_ascii=False),'waiting',''))
 def status(self,pid):
  row=self.db.execute('SELECT * FROM pe_followups WHERE pe_id=?',(pid,)).fetchone()
  if not row:return None
  return {'status':row['state'],'job_id':json.loads(row['request'])['request_id'],'error':row['error']}
 def cards(self):
  rows=self.db.execute("SELECT f.*,p.state AS pe_state,p.created,p.updated,p.error AS pe_error FROM pe_followups f JOIN pe_jobs p ON p.id=f.pe_id LEFT JOIN jobs j ON j.id=f.pe_id WHERE j.id IS NULL ORDER BY p.created DESC").fetchall()
  cards=[]
  for r in rows:
   d=json.loads(r['request']);failed=r['state']=='failed' or r['pe_state'] in ('failed','interrupted')
   state='interrupted' if r['state']=='cancelled' or r['pe_state']=='interrupted' else 'failed' if failed else ('pe_running' if r['pe_state']=='running' else 'pe_queued')
   cards.append({'job_id':r['pe_id'],'status':state,'created':r['created'],'updated':r['updated'],'title':'自动任务 '+r['pe_id'][:8], 'width':d.get('width'),'height':d.get('height'),'frames':d.get('frames'),'config':{'profile':d.get('profile')},'detail':r['pe_error'] or r['error'] or ('正在改写提示词' if state=='pe_running' else '等待提示词改写与预检'),'result':None,'pipeline':True,'request':d})
  return cards
 async def run_once(self):
  rows=self.db.execute("SELECT f.*,p.state AS pe_state,p.result FROM pe_followups f JOIN pe_jobs p ON p.id=f.pe_id WHERE f.state='waiting' AND p.state IN ('succeeded','failed','interrupted')").fetchall()
  for row in rows:
   try:
    if row['pe_state']!='succeeded':raise ValueError('PE改写未成功，未提交视频；请查看改写错误后重新提交。')
    data=json.loads(row['request'])
    # Crash recovery: the video may already have been durably inserted.
    existing=self.db.execute('SELECT id FROM jobs WHERE id=?',(data['request_id'],)).fetchone()
    if not existing:
     data.update(prompt=json.loads(row['result'])['prompt'],prompt_format='pe_v1',prompt_language='en',pe_id=row['pe_id'])
     checked=await self.g.validate_request(Request(data));check=json.loads(checked.body)
     if checked.status>=400 or not check.get('valid') or check.get('submission_allowed') is False:raise ValueError(check.get('error','改写后预检未通过'))
     if check.get('validation_token'):data['validation_token']=check['validation_token']
     latest=self.db.execute('SELECT state FROM pe_followups WHERE pe_id=?',(row['pe_id'],)).fetchone()
     if latest['state']!='waiting':continue
     result=await self.g.submit(Request(data))
     if result.status>=400:raise ValueError('视频提交失败')
    self.db.execute("UPDATE pe_followups SET state='submitted' WHERE pe_id=?",(row['pe_id'],))
   except Exception as exc:
    message=exc.text if isinstance(exc,web.HTTPException) else str(exc)
    self.db.execute("UPDATE pe_followups SET state='failed',error=? WHERE pe_id=? AND state='waiting'",(message[:1000],row['pe_id']))
   self.db.commit()
