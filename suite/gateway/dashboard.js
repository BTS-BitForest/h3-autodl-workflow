'use strict';
const $=id=>document.getElementById(id), terminal=s=>['succeeded','failed','interrupted'].includes(s);
let token=sessionStorage.getItem('h3-token')||'', data=null, seen=new Map(), loading=false, limit=100, epoch=0, videoURL=null;
const labels={pe_queued:'等待改写 / 预检',pe_running:'正在改写',queued:'等待提交',submitting:'正在入队',submitted:'排队中',running:'生成中',succeeded:'已完成',failed:'失败',interrupted:'中断'};
function duration(seconds){const n=Math.max(0,Math.round(seconds));const h=Math.floor(n/3600),m=Math.floor(n%3600/60),s=n%60;return (h?h+'小时 ':'')+(m?m+'分 ':'')+s+'秒';}
function timingHTML(j){const t=j.timing;if(!t)return '';let parts=[];
 if(t.generation_seconds!=null)parts.push('生成耗时 '+duration(t.generation_seconds));
 else if(t.running_seconds!=null)parts.push('已生成 '+duration(t.running_seconds));
 else if(terminal(j.status))parts.push('生成耗时：历史起止记录不足');
 if(t.queue_seconds!=null)parts.push('排队 '+duration(t.queue_seconds));
 parts.push((terminal(j.status)?'总耗时（含排队） ':'提交至今 ')+duration(t.total_seconds));
 return '<div class="meta timing">'+esc(parts.join(' · '))+'</div>';}
function progressHTML(j){if(j.status!=='running')return '';const p=j.progress||{};const n=p.percent;const measured=typeof n==='number';return `<div class="live"><div>${esc(p.stage||'等待实时进度')}${measured?' · '+esc(p.value)+' / '+esc(p.total)+(p.sampling?' 步':'')+' · '+esc(n)+'%':''}${p.connected===false?'（连接中断，等待恢复）':''}</div><progress aria-label="当前节点进度" max="100" ${measured?'value="'+Number(n)+'"':''}></progress><div class="meta">${p.sampling?'这是采样阶段进度，之后还需解码与保存。':'当前阶段进度；没有计数时显示活动条，不估算百分比。'}</div></div>`;}
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path){const r=await fetch(path,{headers:{Authorization:'Bearer '+token},cache:'no-store'});if(!r.ok)throw Error(r.status===401?'密钥无效，请重新连接。':'请求失败：HTTP '+r.status);return r;}
function note(s){$('notice').textContent=s;$('notice').hidden=false;}
function render(){if(!data)return;const opened=new Set([...document.querySelectorAll('details[open]')].map(x=>x.dataset.job));const q=$('search').value.toLowerCase(), f=$('filter').value;
 const jobs=data.jobs.filter(j=>(!q||[j.title,j.job_id,j.config?.profile].join(' ').toLowerCase().includes(q))&&(f==='all'||(f==='active'&&!terminal(j.status))||(f==='failed'&&['failed','interrupted'].includes(j.status))||(f==='running'&&j.status==='pe_running')||j.status===f));
 $('jobs').innerHTML=jobs.map(j=>{let pos=j.backend_pending_position?'ComfyUI 队列第 '+j.backend_pending_position+' 位':j.gateway_pending_position?'接口等待第 '+j.gateway_pending_position+' 位':'';const cfg=j.config||{};
 return `<article class="job"><div><h3>${esc(j.title||'未命名镜头')}</h3><div class="meta">${esc(j.job_id)} · ${esc(new Date(j.created*1000).toLocaleString())}</div><div class="meta">${esc(cfg.profile||'旧任务')} · ${esc(cfg.steps??'—')} 步 · ${esc(j.width)} × ${esc(j.height)} · ${esc(j.frames)} 帧 ${pos?' · '+esc(pos):''}</div>${timingHTML(j)}</div><div><span class="badge ${esc(j.status)}">${esc(labels[j.status]||j.status)}</span></div>${progressHTML(j)}<details data-job="${esc(j.job_id)}" ${opened.has(j.job_id)?'open':''}><summary>参考素材、完整提示词与设置${j.detail?'与错误信息':''}</summary><div class="history-content" data-history="${esc(j.job_id)}">${historyHTML(j.job_id)}</div><pre>${esc(JSON.stringify({配置:cfg,信息:j.detail||'暂无错误',结果:j.result},null,2))}</pre></details><div class="actions">${!terminal(j.status)?`<button data-cancel="${esc(j.job_id)}">${['queued','submitted','pe_queued'].includes(j.status)?'取消排队':'中断任务'}</button>`:''}<button data-reuse="${esc(j.job_id)}">重复使用</button><button data-copy="${esc(j.job_id)}">复制编号</button>${j.status==='succeeded'?`<button data-enhance="${esc(j.job_id)}">超分</button><button data-preview="${esc(j.job_id)}">预览</button><button class="primary" data-download="${esc(j.job_id)}">下载 MP4</button>`:''}</div></article>`;
 }).join('')||'<div class="empty">没有符合条件的任务</div>';$('older').hidden=!data.has_more;
}
async function refresh(){if(!token||loading)return;loading=true;const myEpoch=epoch;try{const fresh=await(await api('/v1/dashboard?limit='+limit)).json();if(myEpoch!==epoch)return;
 for(const j of fresh.jobs){if(seen.has(j.job_id)&&!terminal(seen.get(j.job_id))&&terminal(j.status)){const message=(j.title||j.job_id.slice(0,8))+'：'+labels[j.status];note(message);if('Notification'in window&&Notification.permission==='granted')new Notification('H3 任务状态更新',{body:message,tag:j.job_id});}seen.set(j.job_id,j.status);}
 data=fresh;refreshBootstrap();refreshPEState();if(!profilesLoaded)loadProfiles();for(const k of ['queued','running','succeeded','failed'])$(k).textContent=fresh.counts[k];$('workspace').hidden=false;$('login').hidden=true;$('logout').hidden=false;$('connection').textContent='已连接 · '+new Date().toLocaleTimeString();$('error').textContent='';$('backend').textContent=fresh.backend.available?`ComfyUI：${fresh.backend.running} 个运行中，${fresh.backend.pending} 个等待中（包含其他入口提交的任务）`:'ComfyUI 暂不可用，正在显示已保存的任务状态。';render();
 }catch(e){if(myEpoch===epoch){$('connection').textContent='连接中断 · 自动重试';$('error').textContent=e.message;$('login').hidden=false;}}finally{loading=false;}}
$('connect').onclick=()=>{token=$('token').value.trim();if(!token){$('error').textContent='请选择 token.txt 或填写密钥。';return;}epoch++;seen.clear();sessionStorage.setItem('h3-token',token);refresh();};
$('tokenfile').onchange=async e=>{if(e.target.files[0])$('token').value=(await e.target.files[0].text()).trim();};
$('logout').onclick=()=>{epoch++;token='';data=null;seen.clear();sessionStorage.removeItem('h3-token');$('token').value='';$('tokenfile').value='';$('workspace').hidden=true;$('login').hidden=false;$('logout').hidden=true;$('connection').textContent='未连接';$('jobs').textContent='';$('notice').hidden=true;closeVideo();};
$('refresh').onclick=refresh;$('search').oninput=render;$('filter').onchange=render;$('older').onclick=()=>{limit+=100;refresh();};
$('notify').onclick=async()=>{if(!('Notification'in window)){note('浏览器不支持桌面通知，页面内仍会提醒。');return;}const permission=await Notification.requestPermission();note(permission==='granted'?'桌面提醒已开启，请保持此页面打开。':'桌面提醒未获允许，页面内仍会提醒。');};
$('jobs').onclick=async e=>{const b=e.target.closest('button');if(!b)return;const id=b.dataset.download||b.dataset.preview||b.dataset.copy;if(!id)return;try{if(b.dataset.copy){await navigator.clipboard.writeText(id);note('任务编号已复制。');return;}b.disabled=true;const blob=await(await api('/v1/jobs/'+encodeURIComponent(id)+'/video')).blob();const url=URL.createObjectURL(blob);if(b.dataset.preview){closeVideo();videoURL=url;$('video').src=url;$('preview').showModal();}else{const a=document.createElement('a');a.href=url;a.download=id+'.mp4';a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);}}catch(e){$('error').textContent=e.message;}finally{b.disabled=false;}};
function closeVideo(){$('video').pause();$('video').removeAttribute('src');$('video').load();$('preview').close();if(videoURL)URL.revokeObjectURL(videoURL);videoURL=null;}
$('closepreview').onclick=closeVideo;$('preview').addEventListener('cancel',e=>{e.preventDefault();closeVideo();});
async function boot(){
 const ticket=new URLSearchParams(location.hash.slice(1)).get('login');
 if(ticket){history.replaceState(null,'',location.pathname+location.search);token='';sessionStorage.removeItem('h3-token');try{
  const r=await fetch('/v1/browser-login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket}),cache:'no-store'});
  if(!r.ok)throw Error('一键登录链接已过期，请重新双击启动文件。');
  token=(await r.json()).token;sessionStorage.setItem('h3-token',token);
 }catch(e){$('error').textContent=e.message;return;}}
 if(token)await refresh();
}
setInterval(refresh,3000);boot();

// Composer uses the existing authenticated, idempotent API. No background GPU work.
let reusedRequest=null, reusedSnapshot=null;
const historyCache=new Map(), assetURLs=new Map();
let references=[], composeBusy=false, draftRequest=null, draftSignature='', peSignature='', peResult=null;
const kinds={png:'image',jpg:'image',jpeg:'image',webp:'image',mp4:'video',mov:'video',webm:'video',wav:'audio',mp3:'audio',flac:'audio',m4a:'audio',ogg:'audio'};
const tagNames={image:'Picture',video:'Video',audio:'Audio'};
function paintReferences(){const n={image:0,video:0,audio:0};$('references').innerHTML=references.map((r,i)=>{r.tag=tagNames[r.kind]+' '+(++n[r.kind]);return `<div class="reference"><button data-ref-preview="${i}" aria-label="浏览素材">${thumbnail(r.kind,r.url)}</button><strong>${esc(r.tag)}</strong><span>${esc(r.file?.name||r.assetInfo?.filename||r.asset)} · ${((r.file?.size||r.assetInfo?.bytes||0)/1048576).toFixed(1)} MB</span><input aria-label="${esc(r.tag)} 用途" data-binding="${i}" value="${esc(r.binding)}" placeholder="用途，例如：主角外观 / 动作参考 / 角色音色"><button data-up="${i}" ${i===0?'disabled':''}>上移</button><button data-remove="${i}">移除</button></div>`;}).join('');}
function addReferences(files){if(composeBusy)return;try{const next=[...references];for(const file of files){const kind=kinds[file.name.split('.').pop().toLowerCase()];if(!kind)throw Error('不支持文件：'+file.name);if(file.size>512*1024**2)throw Error('单个文件不能超过 512 MB');next.push({file,kind,binding:'',asset:null,url:URL.createObjectURL(file)});}for(const [k,max] of Object.entries({image:9,video:3,audio:3}))if(next.filter(r=>r.kind===k).length>max)throw Error('参考素材超限：'+k+' 最多 '+max+' 个');if(next.length>12)throw Error('参考素材合计最多 12 个');references=next;$('dialogue-approved').checked=false;paintReferences();$('submission-status').textContent='素材已添加；预览时上传并校验，不会生成视频。';}catch(e){$('submission-status').textContent=e.message;}}
$('reference-drop').onclick=()=>$('reference-files').click();$('reference-drop').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('reference-files').click();}};
$('reference-files').onchange=e=>{addReferences(e.target.files);e.target.value='';};
for(const ev of ['dragenter','dragover'])$('reference-drop').addEventListener(ev,e=>{e.preventDefault();$('reference-drop').classList.add('over');});
for(const ev of ['dragleave','drop'])$('reference-drop').addEventListener(ev,e=>{e.preventDefault();$('reference-drop').classList.remove('over');});
$('reference-drop').addEventListener('drop',e=>addReferences(e.dataTransfer.files));
$('references').oninput=e=>{if(e.target.dataset.binding!==undefined)references[Number(e.target.dataset.binding)].binding=e.target.value;};
$('references').onclick=e=>{if(composeBusy)return;const b=e.target.closest('button');if(!b)return;if(b.dataset.refPreview!==undefined){showReference(references[Number(b.dataset.refPreview)]);return;}$('dialogue-approved').checked=false;if(b.dataset.remove!==undefined){const removed=references.splice(Number(b.dataset.remove),1)[0];if(removed.file&&removed.url)URL.revokeObjectURL(removed.url);}if(b.dataset.up!==undefined){const i=Number(b.dataset.up);if(i>0)[references[i-1],references[i]]=[references[i],references[i-1]];}paintReferences();};
$('create-sound').onchange=()=>$('dialogue-fields').hidden=$('create-sound').value!=='specified_dialogue';
async function postJSON(path,body){const r=await fetch(path,{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(body)});let j;try{j=await r.json();}catch{throw Error('服务器响应异常：HTTP '+r.status);}if(!r.ok)throw Error(j.error||'请求失败：HTTP '+r.status);return j;}
function uploadReference(ref){return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/v1/assets');xhr.setRequestHeader('Authorization','Bearer '+token);xhr.setRequestHeader('X-Filename',encodeURIComponent(ref.file.name));xhr.upload.onprogress=e=>{if(e.lengthComputable)$('submission-status').textContent='上传 '+ref.file.name+' · '+Math.round(e.loaded/e.total*100)+'%';};xhr.onerror=()=>reject(Error('上传中断，请重试'));xhr.onload=()=>{try{const j=JSON.parse(xhr.responseText);if(xhr.status!==201)throw Error(j.error||'上传失败');ref.asset=j.asset_id;ref.assetInfo=j;resolve();}catch(e){reject(e);}};xhr.send(ref.file);});}
async function compose(submit){if(composeBusy)return;if(!token){$('submission-status').textContent='请先连接服务器';return;}composeBusy=true;const controls=[...$('composer').querySelectorAll('button,input,textarea,select')];controls.forEach(x=>x.disabled=true);try{
 let prompt=$('prompt-input').value.trim();if(!prompt)throw Error('请填写提示词');const mode=$('create-mode').value;const count=k=>references.filter(r=>r.kind===k).length;
 if(count('audio')&&!count('image')&&!count('video'))throw Error('音频需要搭配图片或视频');
 if(['i2v','first_last'].includes(mode)&&(count('video')||count('audio')||count('image')!==(mode==='i2v'?1:2)))throw Error('首帧模式需 1 张图；首尾帧需 2 张图，不接收其他素材');

 for(const ref of references)if(!ref.asset)await uploadReference(ref);
 const [width,height]=$('create-size').value.split('x').map(Number);const payload={workflow:mode==='auto'?(references.length?(count('image')&&!count('video')&&count('audio')<=1?'ref_multi':'ref_all'):'t2v'):mode,prompt,images:references.filter(r=>r.kind==='image').map(r=>r.asset),width,height,frames:Number($('create-frames').value),profile:$('create-profile').value,sound_policy:$('create-sound').value,prompt_format:'raw_v1'};
 if(payload.workflow==='ref_all'){payload.videos=references.filter(r=>r.kind==='video').map(r=>r.asset);payload.audios=references.filter(r=>r.kind==='audio').map(r=>r.asset);}
 if(payload.workflow==='ref_multi'){
  if(!count('image')||count('video')||count('audio')>1)throw Error('多图参考需要 1–9 张图、最多 1 段音频，不接收视频');
  if(count('audio'))payload.audio=references.find(r=>r.kind==='audio').asset;
 }
 if(payload.sound_policy==='specified_dialogue'){
  payload.dialogue_text=$('dialogue-text').value.trim();payload.dialogue_speaker=$('dialogue-speaker').value.trim();
  if(!payload.dialogue_text||!payload.dialogue_speaker)throw Error('请填写中文台词和说话角色');
  if(!$('dialogue-approved').checked)throw Error('请先确认指定对白及声音来源');
  if(count('video'))throw Error('指定对白不接受视频参考');
  const native=$('dialogue-mode').value==='native';
  if(native&&(count('audio')||$('dialogue-voice').value!=='onscreen_dialogue'))throw Error('原生对白只支持画内说话，且不能带音视频参考');
  if(!native&&count('audio')!==1)throw Error('参考音频对白需要上传唯一完整音频');
  const approval_reference='H3面板明确确认当前台词及所选素材';
  payload.control_level='best_effort';
  payload.dialogue_contract={audio_mode:native?'native':'reuse',voice_type:$('dialogue-voice').value,audio_offset_seconds:0,image_bindings:references.filter(r=>r.kind==='image').map(r=>({asset_id:r.asset,sha256:r.assetInfo.sha256,shot_index:1,role:'reference'}))};
  if(native)Object.assign(payload.dialogue_contract,{native_dialogue_approved:true,approval_reference});
  else {const ref=references.find(r=>r.kind==='audio');payload.dialogue_contract.approved_audio={asset_id:ref.asset,sha256:ref.assetInfo.sha256,approved:true,approval_reference};}
 }
 if(reusedRequest && JSON.stringify(formSnapshot())===reusedSnapshot){Object.keys(payload).forEach(k=>delete payload[k]);Object.assign(payload,structuredClone(reusedRequest));delete payload.request_id;delete payload.validation_token;delete payload.ui_state;}
 else if($('pe-enabled').checked){
 const peInput={prompt:$('prompt-input').value.trim(),workflow:payload.workflow,frames:payload.frames,width:payload.width,height:payload.height,images:payload.images,videos:payload.videos||[],audios:payload.audios||(payload.audio?[payload.audio]:[]),bindings:references.map(r=>({label:r.tag,role:r.binding.trim()})),sound_policy:payload.sound_policy,language:'en',...(payload.sound_policy==='specified_dialogue'?{dialogue_text:payload.dialogue_text,dialogue_speaker:payload.dialogue_speaker,dialogue_contract:payload.dialogue_contract,control_level:payload.control_level}:{})};
 const sig=JSON.stringify(peInput);
 if(!peResult||peSignature!==sig){
  if(submit){
   const snapshot=formSnapshot(),signature=JSON.stringify({peInput,payload,snapshot});
   if(signature!==draftSignature||!draftRequest){const rawSeed=$('create-seed').value,seed=rawSeed===''?crypto.getRandomValues(new Uint32Array(1))[0]:Number(rawSeed);if(!Number.isSafeInteger(seed)||seed<0)throw Error('随机种子须为非负安全整数');draftRequest={...payload,ui_state:snapshot,seed,request_id:crypto.randomUUID()};draftSignature=signature;}
   const task=await postJSON('/v1/pe',{...peInput,request_id:draftRequest.request_id,video_request:draftRequest});
   $('submission-status').textContent='已提交自动任务：'+task.id+'。服务器将排队改写、预检并生成视频；可以关闭页面。';note('已入队：PE改写 → 视频生成');refreshPEState();return;
  }
  peResult=null;$('submission-status').textContent='本地PE任务正在入队…';const task=await postJSON('/v1/pe',{...peInput,request_id:crypto.randomUUID()});
  for(let i=0;i<420;i++){await new Promise(r=>setTimeout(r,1000));const state=await(await api('/v1/pe/'+task.id)).json();$('submission-status').textContent=state.status==='queued'?'PE排队中：等待视频任务结束并释放显存。':'千问正在本地改写，期间不会执行视频生成…';if(['failed','interrupted'].includes(state.status))throw Error(state.error||'PE改写失败');if(state.status==='succeeded'){peResult={...state.result,id:task.id};peSignature=sig;break;}}
  if(!peResult)throw Error('等待超时，PE任务编号：'+task.id+'；请检查队列，不会自动生成视频。');
 }
 payload.prompt=peResult.prompt;payload.prompt_format='pe_v1';payload.prompt_language='en';payload.pe_id=peResult.id;
 }
 if(!$('pe-enabled').checked){payload.prompt_format='raw_v1';delete payload.prompt_language;delete payload.pe_id;}
 payload.ui_state=formSnapshot();
 const seedText=$('create-seed').value;if(seedText!==''){const seed=Number(seedText);if(!Number.isSafeInteger(seed)||seed<0)throw Error('随机种子须为非负安全整数');payload.seed=seed;}
 const signature=JSON.stringify(payload);if(signature!==draftSignature||!draftRequest){draftRequest={...payload,request_id:crypto.randomUUID(),seed:payload.seed??crypto.getRandomValues(new Uint32Array(1))[0]};draftSignature=signature;}
 $('submission-status').textContent='校验素材与工作流…';const check=await postJSON('/v1/validate',draftRequest);$('effective-prompt').textContent=check.effective_prompt;$('prompt-preview').hidden=false;$('prompt-preview').open=true;if(!check.valid||check.submission_allowed===false)throw Error(check.error||'预检未通过');
 if(!submit){$('submission-status').textContent='预览完成，尚未提交生成。'+(check.config?.prompt_warnings||[]).join('；');return;}
 $('submission-status').textContent='正在提交…';const job=await postJSON('/v1/jobs',{...draftRequest,...(check.validation_token?{validation_token:check.validation_token}:{})});$('submission-status').textContent='已入队：'+job.job_id+'。重复点击相同内容会返回同一任务；修改描述或设置可新建任务。';note('新视频任务已提交');refresh();
 }catch(e){$('submission-status').textContent=e.message;}finally{composeBusy=false;controls.forEach(x=>x.disabled=false);paintReferences();updatePEChoice();}}
$('preview-prompt').onclick=()=>compose(false);$('submit-video').onclick=()=>compose(true);

let profilesLoaded=false;
async function loadProfiles(){profilesLoaded=true;try{const result=await(await api('/v1/profiles')).json();const titles={physics:'工作流1 · 基础20步＋Sage＋物理0.30（默认）',pdd_sage:'近期使用 · PDD8＋Sage',pdd_sage_stack:'工作流2 · PDD8＋Sage＋六项LoRA'};$('create-profile').innerHTML=result.profiles.map(p=>'<option value="'+esc(p.name)+'">'+esc(titles[p.name]||p.title||p.name)+'</option>').join('');$('create-profile').value=result.default;}catch{profilesLoaded=false;}}
let upscaleSource=null,upscaleDraft=null,upscaleSignature='',upscaleBusy=false;
function chooseUpscale(file){if(upscaleBusy||!file)return;if(!['mp4','mov','webm'].includes(file.name.split('.').pop().toLowerCase())){$('upscale-status').textContent='请选择 MP4、MOV 或 WebM 视频';return;}if(file.size>512*1024**2){$('upscale-status').textContent='视频不能超过 512 MB';return;}upscaleSource={file,asset:null};$('upscale-source').textContent=file.name;}
$('upscale-file').onchange=e=>chooseUpscale(e.target.files[0]);$('upscale-drop').onclick=()=>$('upscale-file').click();$('upscale-drop').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('upscale-file').click();}};
$('upscale-drop').ondragover=e=>e.preventDefault();$('upscale-drop').ondrop=e=>{e.preventDefault();if(e.dataTransfer.files.length!==1){$('upscale-status').textContent='每次请选择一个源视频';return;}chooseUpscale(e.dataTransfer.files[0]);};
$('jobs').addEventListener('click',e=>{const b=e.target.closest('[data-enhance]');if(!b||upscaleBusy)return;upscaleSource={job:b.dataset.enhance};$('upscale-source').textContent='已完成任务：'+upscaleSource.job;$('upscale-submit').scrollIntoView({behavior:'smooth',block:'center'});});
$('upscale-submit').onclick=async()=>{if(upscaleBusy)return;if(!upscaleSource){$('upscale-status').textContent='请先选择源视频';return;}upscaleBusy=true;$('upscale-submit').disabled=true;try{const source=upscaleSource;if(source.file&&!source.asset){$('upscale-status').textContent='正在上传源视频…';await uploadReference(source);}const payload={workflow:'video_enhance',operation:'upscale',target_height:Number($('upscale-height').value),...(source.job?{source_job_id:source.job}:{video:source.asset})};const signature=JSON.stringify(payload);if(signature!==upscaleSignature){upscaleSignature=signature;upscaleDraft={...payload,request_id:crypto.randomUUID()};}const check=await postJSON('/v1/validate',upscaleDraft);if(!check.submission_allowed)throw Error(check.error||'超分模型尚未就绪');const job=await postJSON('/v1/jobs',{...upscaleDraft,validation_token:check.validation_token});$('upscale-status').textContent='超分已入队：'+job.job_id;refresh();}catch(e){$('upscale-status').textContent=e.message;}finally{upscaleBusy=false;$('upscale-submit').disabled=false;}};

async function refreshPEState(){try{const p=await(await api('/v1/pe')).json();$('pe-state').textContent='本地 PE：'+p.model+' · '+(p.ready?'模型就绪':'模型准备中')+' · '+p.queue.filter(x=>x.status==='running').length+' 个改写中 / '+p.queue.filter(x=>x.status==='queued').length+' 个排队。提示词改写与视频生成依次执行。';}catch{$('pe-state').textContent='本地 PE 服务尚未就绪';}}

function updatePEChoice(){
 const on=$('pe-enabled').checked;
 $('preview-prompt').textContent=on?'PE 改写 / 预览提示词':'预检 / 预览原文';
 $('pe-help').textContent=on?'开启：本地千问改写为英文描述，保留中文对白；先预览，再提交视频。PE 不分析参考音视频内容，请描述其用途。':'关闭：不调用 PE，不自动翻译正文。直接提交用户原文；仍校验素材、参数和对白。';
 try{localStorage.setItem('h3-pe-enabled',String(on));}catch{}
}
try{const saved=localStorage.getItem('h3-pe-enabled');if(saved!==null)$('pe-enabled').checked=saved==='true';}catch{}
$('pe-enabled').onchange=()=>{peResult=null;peSignature='';$('prompt-preview').hidden=true;updatePEChoice();};
$('dialogue-mode').onchange=()=>{$('dialogue-approved').checked=false;if($('dialogue-mode').value==='native')$('dialogue-voice').value='onscreen_dialogue';};
for(const id of ['dialogue-text','dialogue-speaker','dialogue-voice'])$(id).addEventListener('input',()=>{$('dialogue-approved').checked=false;});
updatePEChoice();

const dimensions={480:{'16:9':[864,480],'9:16':[480,864],'1:1':[480,480],'4:3':[640,480],'3:4':[480,640]},768:{'16:9':[1344,768],'9:16':[768,1344],'1:1':[768,768],'4:3':[1024,768],'3:4':[768,1024]}};
function updateSize(){const dims=dimensions[$('resolution-tier').value]?.[$('aspect-ratio').value];if(dims)$('create-size').value=dims.join('x');$('size-hint').textContent='实际生成 '+$('create-size').value.replace('x',' × ')+'；按模型的32像素网格对齐，16:9 档位为近似比例。';}
function updateDuration(){const sec=Number($('duration-seconds').value);$('duration-label').textContent=sec+' 秒';$('create-frames').value=Math.min(345,5+17*Math.max(0,Math.round((sec*24-5)/17)));showDurationHint();}
function showDurationHint(){$('duration-hint').textContent='实际 '+(Number($('create-frames').value)/24).toFixed(3)+' 秒 / '+$('create-frames').value+' 帧。H3 帧数须为5＋17×n，不插帧或变速。';}
$('resolution-tier').onchange=()=>{if($('aspect-ratio').value==='custom')$('aspect-ratio').value='16:9';updateSize();};$('aspect-ratio').onchange=updateSize;$('duration-seconds').oninput=updateDuration;
updateSize();updateDuration();
function formSnapshot(){return {version:3,prompt:$('prompt-input').value,mode:$('create-mode').value,size:$('create-size').value,tier:$('resolution-tier').value,ratio:$('aspect-ratio').value,seconds:$('duration-seconds').value,frames:$('create-frames').value,seed:$('create-seed').value,profile:$('create-profile').value,pe:$('pe-enabled').checked,sound:$('create-sound').value,speaker:$('dialogue-speaker').value,dialogue:$('dialogue-text').value,dialogueMode:$('dialogue-mode').value,voice:$('dialogue-voice').value,approved:$('dialogue-approved').checked,references:references.map(r=>({asset:r.asset,kind:r.kind,binding:r.binding,tag:r.tag}))};}
function thumbnail(kind,url){if(kind==='image')return url?'<img class="thumb" alt="参考图缩略图" src="'+esc(url)+'">':'图片 · 点击浏览';if(kind==='video')return url?'<video class="thumb" muted preload="metadata" src="'+esc(url)+'#t=0.1"></video>':'视频 · 点击浏览';return '<span class="thumb" style="display:grid;place-items:center;font-size:26px">♫</span><span>试听音频</span>';}
async function assetURL(aid){if(assetURLs.has(aid))return assetURLs.get(aid);const url=URL.createObjectURL(await(await api('/v1/assets/'+encodeURIComponent(aid)+'/content')).blob());assetURLs.set(aid,url);return url;}
async function showReference(ref){try{const url=ref.url||await assetURL(ref.asset||ref.asset_id);$('asset-title').textContent=ref.file?.name||ref.assetInfo?.filename||ref.filename||'参考素材';$('asset-body').innerHTML=ref.kind==='image'?'<img alt="参考图片完整预览" src="'+esc(url)+'">':ref.kind==='video'?'<video controls autoplay src="'+esc(url)+'"></video>':'<audio controls autoplay src="'+esc(url)+'"></audio>';$('asset-preview').showModal();}catch(e){note(e.message);}}
function closeAsset(){$('asset-body').innerHTML='';$('asset-preview').close();}
$('close-asset').onclick=closeAsset;$('asset-preview').addEventListener('cancel',e=>{e.preventDefault();closeAsset();});
async function getHistory(id){if(!historyCache.has(id))historyCache.set(id,await(await api('/v1/jobs/'+encodeURIComponent(id)+'/detail')).json());return historyCache.get(id);}
function historyHTML(id){const h=historyCache.get(id);if(!h)return '<p>展开后加载完整记录…</p>';return ['image','video','audio'].map(kind=>'<h4>'+({image:'参考图',video:'参考视频',audio:'参考音频'}[kind])+'</h4><div class="asset-grid">'+h.assets.filter(a=>a.kind===kind).map(a=>'<div class="asset-card"><button data-history-asset="'+esc(a.asset_id)+'" data-kind="'+esc(kind)+'" '+(a.missing?'disabled':'')+'>'+thumbnail(kind,assetURLs.get(a.asset_id))+'</button><p>'+esc(a.filename||a.asset_id)+(a.missing?'（原文件缺失）':'')+'</p></div>').join('')+'</div>').join('')+'<h4>用户输入</h4><pre>'+esc(h.request.ui_state?.prompt||h.pe?.request?.prompt||h.request.prompt||'无提示词')+'</pre><h4>实际完整提示词</h4><pre>'+esc(h.effective_prompt)+'</pre><h4>全部提交设置</h4><pre>'+esc(JSON.stringify(h.request,null,2))+'</pre>';}
async function loadHistory(id){try{const h=await getHistory(id);const el=document.querySelector('[data-history="'+CSS.escape(id)+'"]');if(el)el.innerHTML=historyHTML(id);await Promise.allSettled(h.assets.filter(a=>!a.missing&&['image','video'].includes(a.kind)).map(async a=>{await assetURL(a.asset_id);}));const current=document.querySelector('[data-history="'+CSS.escape(id)+'"]');if(current)current.innerHTML=historyHTML(id);}catch(e){note('历史详情加载失败：'+e.message);}}
$('jobs').addEventListener('toggle',e=>{if(e.target.matches('details[data-job]')&&e.target.open)loadHistory(e.target.dataset.job);},true);
$('jobs').addEventListener('click',async e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.historyAsset){showReference({asset:b.dataset.historyAsset,kind:b.dataset.kind});return;}if(!b.dataset.reuse)return;if(composeBusy){note('请等当前提交窗口操作完成后再复用。');return;}b.disabled=true;try{await reuseJob(b.dataset.reuse);}catch(e){note('无法复用：'+e.message);}finally{b.disabled=false;}});
async function reuseJob(id){const h=await getHistory(id),d=h.request,u=d.ui_state||{};if(h.assets.some(a=>a.missing))throw Error('原素材已缺失，不能完整恢复该任务');
 if(d.workflow==='video_enhance'){upscaleSource=d.source_job_id?{job:d.source_job_id}:{asset:d.video};if(![...$('upscale-height').options].some(o=>o.value===String(d.target_height||768)))$('upscale-height').add(new Option(String(d.target_height),String(d.target_height)));$('upscale-height').value=String(d.target_height||768);$('upscale-source').textContent='复用超分任务 '+id;upscaleDraft=null;upscaleSignature='';$('upscale-submit').scrollIntoView({behavior:'smooth'});note('已填入超分窗口，未提交。');return;}
 references=h.assets.map(a=>({asset:a.asset_id,kind:a.kind,assetInfo:a,binding:u.references?.find(r=>r.asset===a.asset_id)?.binding||h.pe?.request?.bindings?.find(r=>r.label===a.label)?.role||'',tag:a.label,url:assetURLs.get(a.asset_id)}));
 $('prompt-input').value=u.prompt??h.pe?.request?.prompt??d.prompt;
 const mode=u.mode||h.config.mode||d.workflow;if(![...$('create-mode').options].some(o=>o.value===mode))$('create-mode').add(new Option(mode,mode));$('create-mode').value=mode;
 $('create-size').value=u.size||[d.width||864,d.height||480].join('x');$('resolution-tier').value=u.tier||String(Math.min(d.width||864,d.height||480)===768?768:480);$('aspect-ratio').value=u.ratio||Object.keys(dimensions[$('resolution-tier').value]).find(k=>dimensions[$('resolution-tier').value][k].join('x')===$('create-size').value)||'custom';updateSize();
 $('duration-seconds').value=u.seconds||Math.max(1,Math.min(15,Math.round((d.frames||124)/24)));$('duration-label').textContent=$('duration-seconds').value+' 秒';$('create-frames').value=u.frames||d.frames||124;showDurationHint();
 $('create-seed').value=u.seed??String(d.seed??7);$('create-profile').value=d.profile||h.config.profile||'physics';$('pe-enabled').checked=u.pe??(d.prompt_format==='pe_v1');$('create-sound').value=d.sound_policy||'no_voice';$('create-sound').onchange();$('dialogue-text').value=d.dialogue_text||'';$('dialogue-speaker').value=d.dialogue_speaker||'';$('dialogue-mode').value=d.dialogue_contract?.audio_mode||'reuse';$('dialogue-voice').value=d.dialogue_contract?.voice_type||'onscreen_dialogue';$('dialogue-approved').checked=u.approved??!!d.dialogue_contract;
 peResult=null;peSignature='';draftRequest=null;draftSignature='';paintReferences();updatePEChoice();reusedRequest=h.config?.stage==='PE自动任务'?null:structuredClone(d);reusedSnapshot=JSON.stringify(formSnapshot());$('prompt-preview').hidden=false;$('effective-prompt').textContent=h.effective_prompt;$('submission-status').textContent='已恢复原任务的提示词、素材和设置，尚未提交。未修改时保留原始种子及完整接口参数；修改后按窗口设置重新预检。';$('composer').scrollIntoView({behavior:'smooth'});note('已填入提交窗口，未提交视频。');
 await Promise.allSettled(references.filter(r=>!r.url&&['image','video'].includes(r.kind)).map(async r=>{r.url=await assetURL(r.asset);}));paintReferences();
}

$('jobs').addEventListener('click',async e=>{const b=e.target.closest('[data-cancel]');if(!b)return;if(!confirm('确认中断这个任务？已生成的部分不会作为完整视频交付，提示词和素材会保留。'))return;b.disabled=true;try{const result=await postJSON('/v1/jobs/'+encodeURIComponent(b.dataset.cancel)+'/cancel',{});note(result.cancelled?'已发送中断请求；后端可能需要片刻释放显存。':'任务状态已变化，正在刷新。');await refresh();}catch(err){note('中断失败：'+err.message);}finally{b.disabled=false;}});

async function refreshBootstrap(){try{const s=await(await api('/v1/bootstrap')).json();const ready=s.state==='complete';$('bootstrap-panel').hidden=ready;$('bootstrap-retry').hidden=!['failed','pending'].includes(s.state);const n=Number(s.bytes)||0,total=Number(s.size)||0;$('bootstrap-text').textContent=(s.state==='failed'?'下载暂时失败，自动重试耗尽后可手动重试：':s.phase==='verifying'?'正在校验模型：':'正在准备模型：')+(s.file||'等待下载启动')+' · 已完成 '+(s.completed_files||0)+' / '+(s.file_count||33)+' 个文件'+(total?' · 当前文件 '+(n/1073741824).toFixed(2)+' / '+(total/1073741824).toFixed(2)+' GiB':'')+(s.source?' · '+s.source:'')+(s.download_MB_s!=null?' · 总下载速度 '+s.download_MB_s+' MB/s':'');if(total)$('bootstrap-progress').value=Math.min(100,n/total*100);else $('bootstrap-progress').removeAttribute('value');}catch(e){$('bootstrap-panel').hidden=false;$('bootstrap-text').textContent='模型初始化状态暂不可用：'+e.message;}}
$('bootstrap-retry').onclick=async()=>{try{const r=await fetch('/v1/bootstrap/retry',{method:'POST',headers:{Authorization:'Bearer '+token}});if(!r.ok)throw Error('启动失败');await refreshBootstrap();}catch(e){note('重试下载失败：'+e.message);}};
