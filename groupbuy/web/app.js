'use strict';
let csrf = '', initialized = false, toastTimer;
let providers = [], availableKeys = [];
let modelSettingsChanged = false;
const $ = id => document.getElementById(id);
const statuses = {MODEL_READY:'API 工具调用通过',MODEL_CHECKING:'正在测试 API',PENDING:'待办',RUNNING:'运行中',COMPLETE:'完成',REVIEW:'需复核',STOPPED:'已停止',PAUSED:'暂停',STARTING:'启动中',INCOMPLETE:'未完成',INTERRUPTED:'已中断',ATTENTION_REQUIRED:'需处理',WAITING_INPUT:'等待本地输入',UI_REQUIRED:'请允许界面操作',UI_UNAVAILABLE:'缺少界面依赖',NO_PENDING_TASKS:'本批无待办'};
const errors = {api_key_required:'请填写所选服务商的 API Key，或设置对应密钥环境变量。',model_request_rejected:'服务端拒绝请求格式或参数，请核对协议、模型及推理强度。',model_endpoint_not_found:'API 路径不存在，请核对基础地址和协议。',model_auth_failed:'密钥或账户权限无效，请核对所选服务商。',model_rate_limited:'API 限流，请稍后手动重试。',model_service_unavailable:'模型服务暂时不可用。',model_connection_failed:'连接失败或超时，请检查模型网络。',model_tools_not_supported:'未返回预期工具调用，请确认模型支持工具调用。',model_response_incomplete:'模型响应未完整结束，已停止且未执行部分工具。',model_base_url_required:'请填写兼容 API 的基础地址。',invalid_model_base_url:'API 基址格式不正确，请移除凭据、查询参数或接口后缀。',invalid_reasoning_effort:'当前协议或模型设置不支持此推理强度。',invalid_ui_run:'请重启网页服务并核对 API 设置。',agent_model_required:'请填写模型名称。',capture_not_enabled:'先保存任务，再获取自动采集地址。',capture_port_in_use:'自动采集端口已被使用，请检查是否另一个 Agent 正在运行。',optional_ui_dependencies_missing:'请先执行 setup.ps1 -WithUI 安装界面依赖。',ui_not_enabled:'请勾选允许正常操作小程序。',verification_or_login:'需要正常登录或人工处理验证后重试。',capture_incomplete_or_not_reporting:'没有收到完整分页，请检查 Reqable 报告服务器和目标平台抓包。',foreground_changed:'小程序失去前台，已停止。',invalid_ui_tasks:'请按每行「城市 | 搜索词」填写任务。',manual_retry_required:'先处理停止原因，再显式重试任务。',task_configuration_changed:'任务配置与旧进度不一致，请保存为新的任务工作区。',ambiguous_or_missing_window:'未找到唯一小程序窗口，请检查窗口标题和进程名称。'};
Object.assign(errors, {ui_calibration_required:'任务的地点中心、范围或上报仍待校准，已阻止启动；不会发送模型请求。',observed_control_missing:'当前页面没有识别到配置的控件文字，请校准搜索或筛选步骤。',ambiguous_observed_control:'目标文字出现多处，无法安全点击，请配置唯一可见的标签。',search_input_outline_not_unique:'没有识别到唯一搜索框边框，请回到首页或改用输入框文字定位。',target_or_filter_not_visible:'结果页未同时显示目标、城市和筛选条件，请检查页面与配置。'});
Object.assign(statuses,{PASS:'模拟检查通过',FAIL:'模拟检查未通过',SIMULATING:'模拟链路运行中'});
Object.assign(errors,{page_network_error:'页面提示网络异常，已停止采集。先核对正常访问，不自动更换网络或账号。',loading_timeout:'页面持续加载，已停止采集，请先恢复正常访问。',window_changed:'窗口位置或尺寸已变化，已停止。恢复稳定窗口后显式重试。'});
errors.agent_no_progress='模型连续检查或调用失败，任务没有进展，已提前停止。请核对当前阶段和模型工具能力后继续。';
function message(text,error=false) {
  clearTimeout(toastTimer);
  $('message').textContent=text;
  $('message').className=error?'error':'';
  $('message').style.display='block';
  if(!error) toastTimer=setTimeout(()=>{$('message').style.display='none';},8000);
}
Object.assign(statuses,{CAPTURE_LISTENING:'等待新请求上报',CAPTURE_CONNECTED:'上报连接已验证'});
Object.assign(errors,{capture_no_reports:'未收到上报，请检查 Reqable 规则、完整上传地址，并产生新的小程序请求。',report_not_authorized:'上传地址不匹配，请重新复制完整的本机上传地址。',unsupported_report_encoding:'请将 Reqable 压缩设为 Gzip 或无。',invalid_report:'收到的内容不是有效 HAR 上报，请核对报告服务器设置。',invalid_probe_duration:'测试时长须为 0.1–300 秒。',temp_directory_required:'请通过 start-ui.ps1 启动，或将 GB_TEMP_DIR、TEMP、TMP 指向 D 盘。'});
const toolLabels={check_environment:'检查环境',get_status:'规划下一步',collect_ui:'搜索、筛选与滚动',export_har:'核验上报和分页',process_task:'解析、校验与导出',unknown:'未知工具（已阻止）'};
async function api(path,body) {
  const res=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-Groupbuy-CSRF':csrf}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});
  const data=await res.json();
  if(!res.ok) throw new Error(errors[data.code]||data.code||'操作失败');
  return data;
}
function td(text='') { const el=document.createElement('td');el.textContent=text;return el; }
function connectionSummary(state) {
  const checks=state.connection_checks||{}, labels={passed:'已通过',failed:'需处理'};
  const env=checks.environment, capture=checks.capture, model=checks.model;
  const parts=[
    '本机环境：'+(labels[env?.status]||'未检查'),
    '上报连接：'+(state.running&&state.operation==='capture_probe'?'测试中':(labels[capture?.status]||'未测试')),
    '模型接口：'+(state.running&&state.operation==='model_check'?'测试中':modelSettingsChanged?'设置已更改，请重新测试':(labels[model?.status]||'未测试')),
    '页面条件：'+(state.mode==='offline'?'虚构样例':state.calibration_required?'待核对':'已人工确认')
  ];
  $('connection-checks').textContent=parts.join(' · ')+(model&&!modelSettingsChanged?' · 最近测试模型：'+model.model:'');
}
function providerHint() {
  const selected=providers.find(p=>p.id===$('provider').value);
  if(!selected)return;
  $('key-hint').textContent=availableKeys.includes(selected.id)?'启动终端已为所选服务商配置密钥，可直接运行。':'仅本次运行使用；也可在终端配置 '+selected.key_env+'。';
  $('provider-hint').textContent=(selected.protocol.startsWith('responses')?'Responses':selected.protocol==='messages'?'Messages':'Chat Completions')+' · 需要支持工具调用，真实账号联调仍待验证。';
  $('base-url').readOnly=selected.id==='openai';
  $('reasoning-effort').disabled=!selected.protocol.startsWith('responses');
}
$('provider').onchange=()=>{
  modelSettingsChanged=true;
  const selected=providers.find(p=>p.id===$('provider').value);
  $('api-key').value='';$('model').value='';
  $('reasoning-effort').value=selected?.id==='responses_compatible'?'medium':'';
  $('base-url').value=selected?.base_url||'';
  providerHint();
};
for(const id of ['model','base-url','reasoning-effort','api-key']) $(id).addEventListener('input',()=>{modelSettingsChanged=true;});
function restoreForm(state) {
  const d=state.form_defaults||{}, f=d.filters||{}, p=d.ui_profile||{};
  $('batch').value=d.batch_size||5;
  $('category').value=f.category||'美食';
  $('range').value=f.range_text||'3km';
  $('config-path').textContent=state.config_path||'保存任务后显示。';
  if(p.window_title_regex) $('window-title').value=p.window_title_regex;
  if(p.list_anchor) $('list-anchor').value=p.list_anchor;
  $('search-mode').value=p.search_input_mode||'text';
  $('calibrated').checked=p.calibration_required===false;
  if(p.process_names) $('process-names').value=p.process_names.join(',');
  const search=(p.steps||[]).find(s=>s.action==='type_search');
  if(search) $('search-label').value=search.text;
  $('filter-steps').value=(p.steps||[]).filter(s=>s.action==='click').map(s=>s.text).join('\n');
}
async function refresh() {
  const state=await api('/api/state');
  if(!Array.isArray(state.providers))throw new Error('后台仍是旧版本，请重启 start-ui.ps1 后刷新网页。');
  availableKeys=state.key_available_providers||[];
  if(!initialized) {
    providers=state.providers||[];
    $('provider').replaceChildren(...providers.map(p=>{const option=document.createElement('option');option.value=p.id;option.textContent=p.label;return option;}));
    $('provider').value=state.configured_provider||'openai';
    $('base-url').value=state.configured_base_url||providers.find(p=>p.id===$('provider').value)?.base_url||'';
    $('tasks').value=state.tasks.map(t=>t.city+' | '+t.search_term+(t.center_name&&t.center_name!==t.search_term?' | '+t.center_name:'')).join('\n');
    $('model').value=state.configured_model;
    $('reasoning-effort').value=state.configured_reasoning_effort||'';
    restoreForm(state);
    initialized=true;
  }
  providerHint();
  connectionSummary(state);
  $('running').textContent=state.running?'Agent 运行中':'已就绪';
  if(state.running&&state.operation==='capture_probe') $('running').textContent='上报测试中';
  if(state.running&&state.operation==='simulation') $('running').textContent='模拟链路运行中';
  if(!state.running&&state.calibration_required) $('running').textContent='待页面校准';
  $('running').dataset.running=String(state.running);
  for(const id of ['run','demo','simulate','save','provider','model','base-url','api-key','model-check']) $(id).disabled=state.running;
  $('capture-probe').disabled=state.running;
  $('run').disabled=state.running||state.calibration_required;
  if(state.running)$('reasoning-effort').disabled=true;
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
    if(task.ui_diagnostic) { const hint=document.createElement('span');hint.className='task-error';hint.textContent='第 '+task.ui_diagnostic.step+' 步 · '+(task.ui_diagnostic.action==='type_search'?'搜索输入':'筛选点击');statusCell.append(hint); }
    const har=td(task.har_ready?'已保存':'待采集');har.className='har-state'+(task.har_ready?' ready':'');
    const target=td(task.search_term);
    if(task.center_name&&task.center_name!==task.search_term) {const center=document.createElement('span');center.className='task-error';center.textContent='中心：'+task.center_name;target.append(center);}
    tr.append(td(task.task_id),td(task.city),target,statusCell,har);
    const action=td();
    if(['STOPPED','REVIEW'].includes(task.status)) {
      const btn=document.createElement('button');btn.className='secondary';btn.textContent='重试';btn.disabled=state.running;
      btn.onclick=()=>perform(async()=>{await api('/api/retry',{task_id:task.task_id});message('任务已回到待办，启动即可重试。');await refresh();});
      action.append(btn);
    }
    tr.append(action);rows.append(tr);
  }
  $('calibration-note').hidden=!state.calibration_required;
  const capture=state.capture_status;
  $('capture-status').textContent=capture?
    (capture.listening?'正在监听':'监听已结束')+' · 收到 POST '+capture.posts_received+' · 有效报告 '+capture.valid_reports+' · 匹配数据 '+capture.accepted_entries+' · 忽略 '+capture.ignored_entries+(capture.last_error?' · '+(errors[capture.last_error]||capture.last_error):''):'';
  const event=(state.events||[]).at(-1);
  $('agent-events').textContent=event?(event.event==='simulation_case'?'正在模拟：'+(simulationNames[event.status]||event.status):event.event==='model_round'?'模型第 '+event.round+' 轮':event.event==='tool_started'?'正在执行：'+(toolLabels[event.tool]||'本地工具'):event.event==='tool_finished'?'工具第 '+event.call+' 次已返回 · '+(statuses[event.status]||event.status):''):'';
  const r=state.result;
  const simulated=r?.backend==='SIMULATED_RESPONSES_SSE';
  $('simulation-details').hidden=!simulated;
  const simulation=$('simulation-results');simulation.replaceChildren();
  if(simulated) {
    const table=document.createElement('table'),body=document.createElement('tbody');
    for(const item of r.scenarios||[]) {const row=document.createElement('tr');row.append(td(simulationNames[item.scenario]||item.scenario),td(item.passed?'通过':'未通过'));body.append(row);}
    table.append(body);simulation.append(table);
  }
  if(r) {
    $('result').textContent=(statuses[r.status]||r.status)+(r.backend==='SCRIPTED_DEMO'?' · 虚构脚本演示，使用独立进度':'')+(r.code?' · '+(errors[r.code]||r.code):'')+(r.rounds!==undefined?' · 模型轮数 '+r.rounds+' / 工具调用 '+r.tool_calls:'')+(r.report_path?'\n报告：'+r.report_path:'');
    if(simulated&&r.report_path) {
      $('result').textContent='完整链路模拟：'+r.cases_passed+' / '+r.cases_total+' 项通过 · 虚构模型与界面 · 本机 HTTP/SSE 与解析导出实际执行 · 不证明真实采集成功\n报告：'+r.report_path;
      $('output-dir').textContent=r.report_path.replace(/[\\/]report\.json$/,'');
    }
    if(r.backend==='SCRIPTED_DEMO'&&r.report_path) $('output-dir').textContent=r.report_path.replace(/[\\/]agent-runs[\\/][^\\/]+$/,'');
  } else $('result').textContent='尚未启动。首次真实使用需要验证页面、API 与自动报告连接。';
}
async function perform(fn) { try { await fn(); } catch(e) { message(e.message,true); } }
$('save').onclick=()=>perform(async()=>{
  const lines=$('tasks').value.split('\n').map(x=>x.trim()).filter(Boolean);
  const tasks=lines.map(line=>{const parts=line.split('|');if(parts.length<2||parts.length>3)throw new Error(errors.invalid_ui_tasks);return{city:parts[0].trim(),search_term:parts[1].trim(),...(parts.length===3?{center_name:parts[2].trim()}:{})};});
  const result=await api('/api/config',{tasks,category:$('category').value,range_text:$('range').value,batch_size:Number($('batch').value),ui_profile:{window_title_regex:$('window-title').value,search_label:$('search-label').value,search_input_mode:$('search-mode').value,calibration_required:!$('calibrated').checked,filter_steps:$('filter-steps').value.split('\n').map(x=>x.trim()).filter(Boolean),list_anchor:$('list-anchor').value,process_names:$('process-names').value.split(',').map(x=>x.trim()).filter(Boolean)}});
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
  message('复制完整上传地址后编辑 Reqable 规则，并勾选报告服务器总开关。');
});
$('copy-capture-url').onclick=()=>perform(async()=>{
  if(!navigator.clipboard) { $('capture-url').focus();$('capture-url').select();message('地址已选中，请按 Ctrl+C 复制。');return; }
  await navigator.clipboard.writeText($('capture-url').value);message('完整上传地址已复制，粘贴到 Reqable 的服务器 URL。');
});
$('doctor').onclick=()=>perform(async()=>{
  const report=await api('/api/doctor');
  $('doctor-result').textContent='Python '+report.python_version+' · '+(report.core_ready?'核心可用':'核心需修复')+' · '+(report.live_environment?'界面依赖可用':'界面依赖未就绪')+(report.checks.length?' · '+report.checks.map(x=>errors[x.code]||x.code).join('；'):'');
});
$('capture-probe').onclick=()=>perform(async()=>{
  await api('/api/capture-probe',{seconds:30});
  message('接收器已启动，请在 30 秒内产生一次新的小程序请求；只检查连接，不保存上报内容。');
  await refresh();
});
async function start(demo,checkOnly=false) {
  await api('/api/run',{demo,check_only:checkOnly,allow_ui:demo||checkOnly?false:$('allow-ui').checked,reasoning_effort:demo||$('reasoning-effort').disabled?'':$('reasoning-effort').value,provider:demo?'openai':$('provider').value,base_url:demo?'':$('base-url').value.trim(),model:demo?'':$('model').value.trim(),api_key:demo?'':$('api-key').value});
  if(checkOnly)modelSettingsChanged=false;
  if(!checkOnly)$('api-key').value='';
  message(checkOnly?'API 工具调用测试已启动，不执行采集。':demo?'离线演示已启动，不发送模型请求。':'Agent 已启动，请保持小程序前台。');
  await refresh();
}
$('model-check').onclick=()=>perform(()=>start(false,true));
$('run').onclick=()=>perform(()=>start(false));
$('demo').onclick=()=>perform(()=>start(true));
const simulationNames={batch_and_resume:'每批 5 项与恢复第 6 项',no_reports:'没有上报',wrong_report_url:'错误上传地址',missing_page:'缺页与修复后重试',verification:'登录验证与限制停止',network_error:'页面网络错误停止',loading_timeout:'持续加载停止',foreground_lost:'失焦停止',wrong_target:'目标与筛选不符',scroll_budget:'滚动上限不作终页',partial_model_response:'模型 SSE 未完成不执行',model_no_progress:'模型空转提前停止'};
$('simulate').onclick=()=>perform(async()=>{await api('/api/simulate',{});message('正在模拟完整链路，无需 Key、微信或 Reqable；原任务和进度不变。');await refresh();});
$('stop').onclick=()=>perform(async()=>{await api('/api/stop',{});message('已请求停止，将在安全动作边界生效。');});
for(const link of document.querySelectorAll('.sidebar nav a')) link.addEventListener('click',()=>{for(const item of document.querySelectorAll('.sidebar nav a'))item.classList.toggle('active',item===link);});
perform(async()=>{csrf=(await api('/api/bootstrap')).csrf;await refresh();setInterval(()=>perform(refresh),2000);});
