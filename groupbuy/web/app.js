'use strict';
let csrf = '', initialized = false, toastTimer;
const $ = id => document.getElementById(id);
const statuses = {PENDING:'待办',RUNNING:'运行中',COMPLETE:'完成',REVIEW:'需复核',STOPPED:'已停止',PAUSED:'暂停',STARTING:'启动中',INCOMPLETE:'未完成',INTERRUPTED:'已中断',ATTENTION_REQUIRED:'需处理',WAITING_INPUT:'等待本地输入',UI_REQUIRED:'请允许界面操作',UI_UNAVAILABLE:'缺少界面依赖',NO_PENDING_TASKS:'本批无待办'};
const errors = {api_key_required:'请填写 API Key，或在启动终端设置 OPENAI_API_KEY。',agent_model_required:'请填写模型名称。',capture_not_enabled:'先保存任务，再获取自动采集地址。',capture_port_in_use:'自动采集端口已被使用，请检查是否另一个 Agent 正在运行。',optional_ui_dependencies_missing:'请先执行 setup.ps1 -WithUI 安装界面依赖。',ui_not_enabled:'请勾选允许正常操作小程序。',verification_or_login:'需要正常登录或人工处理验证后重试。',capture_incomplete_or_not_reporting:'没有收到完整分页，请检查 Reqable 报告服务器和目标平台抓包。',foreground_changed:'小程序失去前台，已停止。',invalid_ui_tasks:'请按每行「城市 | 搜索词」填写任务。',manual_retry_required:'先处理停止原因，再显式重试任务。',task_configuration_changed:'任务配置与旧进度不一致，请保存为新的任务工作区。',ambiguous_or_missing_window:'未找到唯一小程序窗口，请检查窗口标题和进程名称。'};
function message(text,error=false) {
  clearTimeout(toastTimer);
  $('message').textContent=text;
  $('message').className=error?'error':'';
  $('message').style.display='block';
  if(!error) toastTimer=setTimeout(()=>{$('message').style.display='none';},8000);
}
async function api(path,body) {
  const res=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-Groupbuy-CSRF':csrf}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});
  const data=await res.json();
  if(!res.ok) throw new Error(errors[data.code]||data.code||'操作失败');
  return data;
}
function td(text='') { const el=document.createElement('td');el.textContent=text;return el; }
function restoreForm(state) {
  const d=state.form_defaults||{}, f=d.filters||{}, p=d.ui_profile||{};
  $('batch').value=d.batch_size||5;
  $('category').value=f.category||'美食';
  $('range').value=f.range_text||'3km';
  $('config-path').textContent=state.config_path||'保存任务后显示。';
  if(p.window_title_regex) $('window-title').value=p.window_title_regex;
  if(p.list_anchor) $('list-anchor').value=p.list_anchor;
  if(p.process_names) $('process-names').value=p.process_names.join(',');
  const search=(p.steps||[]).find(s=>s.action==='type_search');
  if(search) $('search-label').value=search.text;
}
async function refresh() {
  const state=await api('/api/state');
  if(!initialized) {
    $('tasks').value=state.tasks.map(t=>t.city+' | '+t.search_term).join('\n');
    $('model').value=state.configured_model;
    restoreForm(state);
    initialized=true;
  }
  $('key-hint').textContent=state.api_key_available?'启动终端已配置密钥，可直接运行。':'只在本次运行内存中使用，不保存到文件。';
  $('running').textContent=state.running?'Agent 运行中':'已就绪';
  $('running').dataset.running=String(state.running);
  for(const id of ['run','demo','save']) $(id).disabled=state.running;
  $('stop').disabled=!state.running;
  $('total-count').textContent=state.tasks.length;
  $('complete-count').textContent=state.tasks.filter(t=>t.status==='COMPLETE').length;
  $('pending-count').textContent=state.tasks.filter(t=>['PENDING','RUNNING'].includes(t.status)).length;
  $('blocked-count').textContent=state.tasks.filter(t=>['STOPPED','REVIEW'].includes(t.status)).length;
  $('output-dir').textContent=state.output_dir;
  const rows=$('task-rows');rows.replaceChildren();
  for(const task of state.tasks) {
    const tr=document.createElement('tr'), statusCell=td(), badge=document.createElement('span');
    badge.className='task-status '+task.status.toLowerCase();
    badge.textContent=statuses[task.status]||task.status;
    statusCell.append(badge);
    if(task.code) { const hint=document.createElement('span');hint.className='task-error';hint.textContent=errors[task.code]||task.code;statusCell.append(hint); }
    const har=td(task.har_ready?'已保存':'待采集');har.className='har-state'+(task.har_ready?' ready':'');
    tr.append(td(task.task_id),td(task.city),td(task.search_term),statusCell,har);
    const action=td();
    if(['STOPPED','REVIEW'].includes(task.status)) {
      const btn=document.createElement('button');btn.className='secondary';btn.textContent='重试';btn.disabled=state.running;
      btn.onclick=()=>perform(async()=>{await api('/api/retry',{task_id:task.task_id});message('任务已回到待办，启动即可重试。');await refresh();});
      action.append(btn);
    }
    tr.append(action);rows.append(tr);
  }
  const r=state.result;
  if(r) {
    $('result').textContent=(statuses[r.status]||r.status)+(r.backend==='SCRIPTED_DEMO'?' · 虚构脚本演示，使用独立进度':'')+(r.code?' · '+(errors[r.code]||r.code):'')+(r.rounds!==undefined?' · 模型轮数 '+r.rounds+' / 工具调用 '+r.tool_calls:'')+(r.report_path?'\n报告：'+r.report_path:'');
    if(r.backend==='SCRIPTED_DEMO'&&r.report_path) $('output-dir').textContent=r.report_path.replace(/[\\/]agent-runs[\\/][^\\/]+$/,'');
  } else $('result').textContent='尚未启动。首次真实使用需要验证页面、API 与自动报告连接。';
}
async function perform(fn) { try { await fn(); } catch(e) { message(e.message,true); } }
$('save').onclick=()=>perform(async()=>{
  const lines=$('tasks').value.split('\n').map(x=>x.trim()).filter(Boolean);
  const tasks=lines.map(line=>{const parts=line.split('|');if(parts.length!==2)throw new Error(errors.invalid_ui_tasks);return{city:parts[0].trim(),search_term:parts[1].trim()};});
  const result=await api('/api/config',{tasks,category:$('category').value,range_text:$('range').value,batch_size:Number($('batch').value),ui_profile:{window_title_regex:$('window-title').value,search_label:$('search-label').value,list_anchor:$('list-anchor').value,process_names:$('process-names').value.split(',').map(x=>x.trim()).filter(Boolean)}});
  $('config-path').textContent=result.config_path;
  $('capture-info').hidden=true;
  message('任务已保存，首次配置 Reqable 后开始运行。');
  await refresh();
});
$('capture').onclick=()=>perform(async()=>{
  const info=await api('/api/capture');
  $('capture-url').value=info.report_url;
  $('capture-rules').value=info.rules.join('\n');
  $('capture-info').hidden=false;
  message('将地址和两条匹配规则配置到 Reqable。');
});
async function start(demo) {
  await api('/api/run',{demo,allow_ui:demo?false:$('allow-ui').checked,model:demo?'':$('model').value.trim(),api_key:demo?'':$('api-key').value});
  $('api-key').value='';
  message(demo?'离线演示已启动，不发送模型请求。':'Agent 已启动，请保持小程序前台。');
  await refresh();
}
$('run').onclick=()=>perform(()=>start(false));
$('demo').onclick=()=>perform(()=>start(true));
$('stop').onclick=()=>perform(async()=>{await api('/api/stop',{});message('已请求停止，将在安全动作边界生效。');});
for(const link of document.querySelectorAll('.sidebar nav a')) link.addEventListener('click',()=>{for(const item of document.querySelectorAll('.sidebar nav a'))item.classList.toggle('active',item===link);});
perform(async()=>{csrf=(await api('/api/bootstrap')).csrf;await refresh();setInterval(()=>perform(refresh),2000);});
