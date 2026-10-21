'use strict';
const $ = (id) => document.getElementById(id);
const state = { scan: null, mapping: {}, fields: [], rules: [], range: {}, page: 1, pages: 0,
  selected: null, spans: [], busy: false,
  traceToken: 0, detailToken: 0, detailTab: 'attributes', activeRecord: null, saved: null, demo: false, cacheId:null, user:null, thread:null, browserToken:0, userToken:0, sessionToken:0, columnPages:{} };
const MAPPINGS = [
  ['user','用户 ID','优先按用户区分，缺失时按主机标识',['user.id','userId','user_id']],
  ['thread','会话 ID','同一用户下聚合多条 Trace',['thread.id','threadId','thread_id']],
  ['trace','Trace ID','将同一次链路聚合在一起',['traceId','traceID','trace_id']],
  ['span','Span ID','当前调用自身的唯一标识',['spanId','spanID','span_id']],
  ['parent','父 Span ID','用于还原父子调用关系',['parentSpanID','parentSpanId','parent_span_id']],
  ['name','操作名称','显示在调用列表中',['spanName','name']],
  ['start','开始时间','时间轴定位依据',['start','startTime','start_time']],
  ['duration','持续时间','Span 耗时；请确认下方单位',['duration']],
  ['service','服务名称','服务名称与统计分布',['serviceName','fullServiceName','service.name']],
  ['host','主机名称','用于查找主机的链路',['host.name','hostname']],
  ['status','状态码','0 未设置 / 1 成功 / 2 异常',['statusCode','status.code']],
  ['kind','Span 调用类型','支持 SDK / OTLP 类型编码',['spanKind','kind']],
  ['status_message','状态说明','错误描述或状态附加信息',['statusMessage']],
  ['resource','资源属性','主机、用户等资源信息',['resourceAttributes']],
  ['attributes','Span 属性','输入、输出等业务参数',['spanAttributes']],
  ['event_time','事件时间','事件的时间戳或时间戳数组',['eventsTs']],
  ['event_name','事件名称','与事件时间按数组位置对应',['eventsName']],
  ['event_attributes','事件属性','与事件时间按数组位置对应',['eventsAttributes']]
];
function el(tag, cls = '', value) { const n = document.createElement(tag); if (cls) n.className = cls; if (value !== undefined) n.textContent = value; return n; }
function text(value) { return typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? 'null'; }
function prettyPath(path) { return path.map((p,i) => p === '[]' ? '[*]' : /^[A-Za-z_$][\w$]*$/.test(p) ? (i ? '.' : '') + p : '[' + JSON.stringify(p) + ']').join(''); }
function number(value) { return Number(value || 0).toLocaleString('zh-CN'); }
function duration(value) { if (value === null || value === undefined || !Number.isFinite(value)) return '—'; const n = Math.max(0,value); if (n === 0) return '0 ms'; if (n < .001) return (n*1e6).toLocaleString('en-US',{maximumFractionDigits:1}) + ' μs'; if (n < 1) return (n*1000).toLocaleString('en-US',{maximumFractionDigits:2}) + ' ms'; if (n < 60) return n.toLocaleString('en-US',{maximumFractionDigits:2}) + ' s'; return (n/60).toLocaleString('en-US',{maximumFractionDigits:2}) + ' min'; }
function date(value, full = false) { if (value == null) return '时间未识别'; const d = new Date(value * 1000); if (Number.isNaN(d.getTime())) return '时间超出显示范围'; return full ? d.toLocaleString('zh-CN',{hour12:false}) + '.' + String(d.getMilliseconds()).padStart(3,'0') : d.toLocaleTimeString('zh-CN',{hour12:false}); }
function notice(value, error = false) { $('status').textContent = value; $('status').className = error ? 'error' : ''; }
async function api(path, body) { const response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) }); const data = await response.json(); if (!response.ok) throw new Error(data.error || '请求失败'); return data; }
function busy(value) {
  state.busy=value;document.body.classList.toggle('busy',value);
  for(const id of ['scan-button','source-demo','demo-button','refresh','mapping-open','filters-open','deduplicate','multi-only','trace-sort','search','source-open']) {
    const node=$(id);if(node)node.disabled=value || (['mapping-open','filters-open','deduplicate','multi-only','trace-sort','refresh','search'].includes(id) && !state.scan);
  }
  for(const level of ['users','sessions','traces','spans']) {
    const page=state.columnPages[level] || {page:1,pages:0};
    $(level+'-prev').disabled=value || page.page<=1;$(level+'-next').disabled=value || page.page>=page.pages;
  }
}
function payload() { return {scan_id:state.scan.scan_id,mapping:state.mapping,field_filters:state.rules,deduplicate:$('deduplicate').checked,min_spans:$('multi-only').checked?2:1,sort:$('trace-sort').value,...state.range}; }
function save() { if (state.demo) return; try { localStorage.setItem('officeace-trace-v2',JSON.stringify({root:$('root-path').value,mapping:state.mapping,rules:state.rules,range:state.range,dedupe:$('deduplicate').checked,multiOnly:$('multi-only').checked,sort:$('trace-sort').value})); } catch {} }
function pathSelect(value, optional = true) { const select = el('select'); select.append(new Option(optional ? '未映射 / 不使用' : '选择字段', '')); for(const f of state.fields) { const path = JSON.stringify(f.path); select.append(new Option(prettyPath(f.path),path)); } select.value = value ? JSON.stringify(value) : ''; select.title = value ? prettyPath(value) : ''; select.addEventListener('change',()=>{ select.title = select.value ? prettyPath(JSON.parse(select.value)) : ''; }); return select; }
function guessedMapping() { const mapping = {}; for (const [key,, , aliases] of MAPPINGS) { let found; if(key==='user') found=state.fields.find(f=>JSON.stringify(f.path)===JSON.stringify(['resourceAttributes','user.id'])); if(key==='thread') found=state.fields.find(f=>JSON.stringify(f.path)===JSON.stringify(['spanAttributes','thread.id'])); if(key==='host') found = state.fields.find(f=>JSON.stringify(f.path)===JSON.stringify(['resourceAttributes','host.name'])); if(!found) for(const alias of aliases) { found=state.fields.find(f=>f.path.length===1 && f.path[0]===alias); if(found) break; } if(!found) for(const alias of aliases) { found=state.fields.find(f=>f.path.at(-1)===alias); if(found) break; } if(!found && key==='host') found=state.fields.find(f=>f.path.slice(-2).join('.')==='host.name'); mapping[key] = found?.path || null; } if(!mapping.status) mapping.status=state.fields.find(f=>JSON.stringify(f.path)===JSON.stringify(['status','code']))?.path || null; if(!mapping.status_message) mapping.status_message=state.fields.find(f=>JSON.stringify(f.path)===JSON.stringify(['status','message']))?.path || null; mapping.kind_encoding='auto'; mapping.time_unit = 'auto'; mapping.duration_unit = 'ns'; return mapping; }
function validPath(path) { return path && state.fields.some(f=>JSON.stringify(f.path)===JSON.stringify(path)); }
async function scan(root, demo = false) { if (state.busy) return; busy(true); $('source-error').textContent=''; notice('正在扫描目录并建立索引，数据量较大时请稍候…'); state.traceToken++; state.detailToken++; try { const result = await api('/api/scan',{root}); state.scan = result; state.demo = demo; state.fields = result.fields; state.mapping = guessedMapping(); state.rules = []; state.range={}; $('deduplicate').checked=true; $('multi-only').checked=false; $('trace-sort').value='recent';
    if (!demo && state.saved) { const saved=state.saved; $('multi-only').checked=saved.multiOnly===true; if(['recent','spans_desc','duration_desc'].includes(saved.sort)) $('trace-sort').value=saved.sort; if(['auto','js','otlp'].includes(saved.mapping?.kind_encoding)) state.mapping.kind_encoding=saved.mapping.kind_encoding; for (const [key] of MAPPINGS) if(validPath(saved.mapping?.[key])) state.mapping[key]=saved.mapping[key]; if(['auto','s','ms','us','ns'].includes(saved.mapping?.time_unit)) state.mapping.time_unit=saved.mapping.time_unit; if(['s','ms','us','ns'].includes(saved.mapping?.duration_unit)) state.mapping.duration_unit=saved.mapping.duration_unit; state.rules=(saved.rules || []).filter(r=>validPath(r.path)); state.range=saved.range || {}; $('deduplicate').checked=saved.dedupe!==false; }
    $('root-path').value=result.root; $('source-label').textContent=(demo?'示例数据 / ':'') + result.root; $('source-label').title=result.root; $('scan-info').textContent=`${number(result.record_count)} 条记录 · ${number(result.file_count)} 个文件`; $('source-dialog').close(); $('filter-count').textContent=state.rules.length+(state.range.time_from || state.range.time_to ? 1 : 0); clearTrace(); await query(1,true); if(result.warnings?.length) notice(`已连接。扫描提示：${result.warnings.slice(0,3).join('；')}`,true); save();
  } catch(error) { $('source-error').textContent=error.message; notice(error.message,true); } finally { busy(false); } }
function empty(container,title,description) { const box=el('div','empty compact'); box.append(el('span','empty-symbol','⌕'),el('h3','',title),el('p','',description)); container.replaceChildren(box); }
async function query(page=1,alreadyBusy=false) {
  if(!state.scan || (state.busy && !alreadyBusy)) return;
  if(!alreadyBusy) busy(true);
  const token=++state.browserToken;
  state.userToken++;state.sessionToken++;state.traceToken++;state.detailToken++;
  notice('正在建立用户、会话和链路视图，首次载入需要建立索引…');
  try {
    const result=await api('/api/browse',{...payload(),level:'users',page,page_size:40,search:$('search').value.trim()});
    if(token!==state.browserToken)return;
    state.cacheId=result.cache_id;state.pages=result.pages;state.page=result.page;
    const stats=result.stats;
    for(const name of ['users','sessions','traces','spans']) $('metric-'+name).textContent=number(stats[name]);
    clearTrace();
    renderColumn('users',result);
    const userPath=state.mapping.user?prettyPath(state.mapping.user):'未映射';
    const threadPath=state.mapping.thread?prettyPath(state.mapping.thread):'未映射';
    $('trace-diagnostics').textContent=`用户：${userPath}（缺失时按主机区分） · 会话：${threadPath} · ${number(stats.unassigned)} 条调用未关联会话 · ${number(stats.inferred)} 条沿同一链路关联会话 · 异常 ${number(stats.errors)} 条`;
    $('trace-diagnostics').hidden=false;
    notice(`${number(stats.users)} 个用户 · ${number(stats.sessions)} 个会话 · ${number(stats.traces)} 条链路 · ${number(stats.spans)} 次调用 · ${result.cache_hit?'复用缓存':'首次聚合'} ${result.elapsed_ms} ms`);
    save();
    if(result.items.length) await chooseUser(result.items[0]);
  } catch(error) {notice(error.message,true);}
  finally {if(!alreadyBusy)busy(false);}
}
function childPayload(level,page=1) {
  return {cache_id:state.cacheId,level,page,page_size:level==='spans'?100:40,sort:$('trace-sort').value,
    user_key:state.user?.key,thread_key:level==='traces'||level==='spans'?state.thread?.key:undefined,
    trace_key:level==='spans'?state.selected?.key:undefined};
}
function clearColumn(level,message) {
  empty($(level+'-list'),'等待选择',message);
  $(level+'-count').textContent='0';$(level+'-page').textContent='0 / 0';
  $(level+'-prev').disabled=true;$(level+'-next').disabled=true;
  state.columnPages[level]={page:1,pages:0};
}
function clearTrace() {
  state.user=null;state.thread=null;state.selected=null;state.activeRecord=null;
  for(const [level,hint] of [['sessions','选择用户，查看会话'],['traces','选择会话，查看链路'],['spans','选择链路，查看调用']])clearColumn(level,hint);
  empty($('detail-content'),'从用户开始探索','点击用户查看统计，点击调用查看 Span 详情');
  $('detail-title').textContent='详情';$('selection-path').textContent='用户 → 会话 → 链路 → 调用';
  $('detail').classList.remove('mobile-open');
}
function markSelected(level,key) {
  for(const row of $(level+'-list').querySelectorAll('[data-key]')) {
    const selected=row.dataset.key===key;row.classList.toggle('selected',selected);row.setAttribute('aria-pressed',String(selected));
  }
}
function updatePath() {
  $('selection-path').textContent=[state.user?.label,state.thread?.label,state.selected?.label].filter(Boolean).join(' → ') || '用户 → 会话 → 链路 → 调用';
  $('selection-path').title=$('selection-path').textContent;
}
function renderColumn(level,result) {
  state.columnPages[level]={page:result.page,pages:result.pages};
  $(level+'-count').textContent=number(result.total);
  $(level+'-page').textContent=`${result.page} / ${result.pages || 1}`;
  $(level+'-prev').disabled=result.page<=1;$(level+'-next').disabled=result.page>=result.pages;
  const list=$(level+'-list');list.replaceChildren();list.scrollTop=0;
  if(!result.items.length) {empty(list,'暂无记录',level==='users'?'调整搜索或筛选范围':'当前选择下没有匹配记录');return;}
  for(const item of result.items) {
    const card=el('button',`browse-card ${level}-card`);card.type='button';card.dataset.key=level==='spans'?String(item.record_id):item.key;
    const top=el('div','browse-card-top');
    const label=level==='spans'?item.name || '未命名调用':item.label;
    if(level==='users') {
      const avatar=el('span','user-avatar',label.slice(0,1).toUpperCase());top.append(avatar);
    }
    const title=el('strong','',label);title.title=label;top.append(title);
    card.append(top);
    if(level==='users') {
      const kind=JSON.parse(item.key)[0];
      card.append(el('div','card-subtitle',kind==='user'?'用户 ID':kind==='host'?'主机标识 · 未提供用户 ID':'未识别用户分组'));
      card.append(el('div','card-counts',`${number(item.sessions)} 会话 · ${number(item.traces)} 链路`));
      card.append(el('div','card-bottom',`${number(item.spans)} 调用 · ${number(item.errors)} 异常`));
      card.addEventListener('click',()=>chooseUser(item,true));
    } else if(level==='sessions') {
      card.append(el('div','card-subtitle',item.unassigned===item.spans?'没有可确定的 thread.id':'THREAD · 会话'));
      card.append(el('div','card-counts',`${number(item.traces)} 链路 · ${number(item.spans)} 调用`));
      card.append(el('div','card-bottom',date(item.first,true)));
      card.addEventListener('click',()=>chooseSession(item));
    } else if(level==='traces') {
      const identity=JSON.parse(item.key);card.append(el('div','mono card-subtitle',identity[0]==='trace'?identity[1]:'缺少 Trace ID'));
      card.append(el('div','card-counts',`${number(item.spans)} 调用 · ${number(item.errors)} 异常`));
      card.append(el('div','card-bottom',`${date(item.first)} · ${duration(item.first===null?null:item.last-item.first)}`));
      card.addEventListener('click',()=>chooseTrace(item));
    } else {
      const operation=Otel.operation(item.name);
      if(operation)top.prepend(el('span',`pill operation-pill operation-${operation.kind}`,operation.label));
      if(item.error)top.append(el('span','pill error-pill','异常'));
      card.append(el('div','card-subtitle',item.service || '未提供服务'));
      card.append(el('div','mono card-subtitle',item.span_id || '缺少 Span ID'));
      card.append(el('div','card-bottom',`${date(item.stamp)} · ${duration(item.duration)}`));
      card.addEventListener('click',()=>selectSpan(item));
    }
    list.append(card);
  }
}
async function chooseUser(item,open=false) {
  const token=++state.userToken;
  state.sessionToken++;state.traceToken++;state.detailToken++;
  state.user=item;state.thread=null;state.selected=null;state.activeRecord=null;
  markSelected('users',item.key);updatePath();
  for(const [level,hint] of [['sessions','正在载入会话…'],['traces','选择会话，查看链路'],['spans','选择链路，查看调用']])clearColumn(level,hint);
  $('detail-title').textContent='用户统计';$('detail-content').replaceChildren(el('div','trace-loading','正在统计用户信息…'));
  if(open)$('detail').classList.add('mobile-open');
  else $('detail').classList.remove('mobile-open');
  const results=await Promise.allSettled([
    api('/api/browse',childPayload('sessions')),
    api('/api/browse',childPayload('user_stats'))
  ]);
  if(token!==state.userToken)return;
  if(results[1].status==='fulfilled')renderUserStats(results[1].value);
  else empty($('detail-content'),'统计读取失败',results[1].reason.message);
  if(results[0].status==='fulfilled') {
    renderColumn('sessions',results[0].value);
    if(results[0].value.items.length)await chooseSession(results[0].value.items[0]);
  } else notice(results[0].reason.message,true);
}
async function chooseSession(item) {
  const token=++state.sessionToken;state.traceToken++;state.detailToken++;
  state.thread=item;state.selected=null;state.activeRecord=null;
  if(state.userStats?.user.key===state.user?.key){$('detail-title').textContent='用户统计';renderUserStats(state.userStats);}
  markSelected('sessions',item.key);updatePath();
  clearColumn('traces','正在载入链路…');clearColumn('spans','选择链路，查看调用');
  try {
    const result=await api('/api/browse',childPayload('traces'));
    if(token!==state.sessionToken)return;
    renderColumn('traces',result);
    if(result.items.length)await chooseTrace(result.items[0]);
  }catch(error){if(token===state.sessionToken)notice(error.message,true);}
}
async function chooseTrace(item) {
  const token=++state.traceToken;state.detailToken++;state.selected=item;state.activeRecord=null;
  if(state.userStats?.user.key===state.user?.key){$('detail-title').textContent='用户统计';renderUserStats(state.userStats);}
  markSelected('traces',item.key);updatePath();clearColumn('spans','正在载入调用…');
  try {
    const result=await api('/api/browse',childPayload('spans'));
    if(token!==state.traceToken)return;
    state.spans=result.items;renderColumn('spans',result);
  }catch(error){if(token===state.traceToken)notice(error.message,true);}
}
function renderUserStats(result) {
  state.userStats=result;
  const item=result.user,body=el('div','detail-body');
  body.append(el('span','pill','用户概览'),el('h3','',item.label));
  const kind=JSON.parse(item.key)[0];
  body.append(el('p','note',kind==='user'?'按 user.id 聚合；统计基于当前筛选范围内保留的完整链路。':kind==='host'?'此分组使用主机标识，源记录没有用户 ID，不代表已识别到具体个人。':'源记录缺少用户与主机标识，归入未识别分组。'));
  const stats=el('div','user-stats-grid');
  for(const [label,value] of [['会话',item.sessions],['链路',item.traces],['调用',item.spans],['异常',item.errors],['主机',item.hosts],['服务',item.services]]) {
    const box=el('div');box.append(el('strong','',number(value)),el('span','',label));stats.append(box);
  }
  body.append(stats);
  const period=el('div','user-period');period.append(el('span','detail-section-title','观测时间范围'),el('div','',date(item.first,true)),el('div','',date(item.last,true)),el('p','note',`Span 累计耗时 ${duration(item.total_duration)}（并行调用会重叠，不等于会话用时）`));body.append(period);
  if(item.unassigned)body.append(el('p','data-warning',`${number(item.unassigned)} 条调用未关联会话，保留在「未关联会话」中。`));
  for(const [key,label] of [['hosts','主机分布'],['services','服务分布'],['operations','常见调用']]) {
    const group=el('section','stats-distribution');group.append(el('h4','detail-section-title',label+' · 前 20 项'));
    for(const row of result[key]) {
      const line=el('div','distribution-row');line.append(el('span','',row.name),el('b','',number(row.spans)));group.append(line);
    }
    if(!result[key].length)group.append(el('p','note','未提供对应信息'));
    body.append(group);
  }
  $('detail-content').replaceChildren(body);$('detail-content').scrollTop=0;
}
async function pageColumn(level,delta) {
  if(state.busy)return;
  const current=state.columnPages[level];const page=current.page+delta;
  if(page<1 || page>current.pages)return;
  if(level==='users'){await query(page);return;}
  const token={users:state.userToken,sessions:state.sessionToken,traces:state.traceToken};
  const request=childPayload(level,page);$(level+'-prev').disabled=true;$(level+'-next').disabled=true;
  try {
    const result=await api('/api/browse',request);
    if(token.users!==state.userToken || token.sessions!==state.sessionToken || token.traces!==state.traceToken)return;
    renderColumn(level,result);
    if(level==='sessions'){if(result.items.length)await chooseSession(result.items[0]);}
    if(level==='traces'){if(result.items.length)await chooseTrace(result.items[0]);}
    if(level==='spans')state.spans=result.items;
  }catch(error){notice(error.message,true);$(level+'-prev').disabled=current.page<=1;$(level+'-next').disabled=current.page>=current.pages;}
}

function lookup(record,path) { if(!path) return undefined; function visit(value,index) { if(index===path.length) return value; if(value===null || typeof value!=='object') return undefined; if(path[index]==='[]') { if(!Array.isArray(value)) return undefined; return value.map(x=>visit(x,index+1)).filter(x=>x!==undefined); } return Object.prototype.hasOwnProperty.call(value,path[index])?visit(value[path[index]],index+1):undefined; } return visit(record,0); }
function timestamp(value) { if(Array.isArray(value)) return Otel.hrtime(value); if(value===null || value===undefined || typeof value==='boolean') return null; const v=Number(value); if(typeof value==='number' || (typeof value==='string' && value.trim()!=='' && Number.isFinite(v))) { const unit=state.mapping.time_unit || 'auto'; const divisor={s:1,ms:1e3,us:1e6,ns:1e9}[unit] || (Math.abs(v)>=1e17?1e9:Math.abs(v)>=1e14?1e6:Math.abs(v)>=1e11?1e3:1); return Number.isFinite(v)?v/divisor:null; } const n=Date.parse(value); return Number.isNaN(n)?null:n/1000; }
function events(record) {
  const values=['event_time','event_name','event_attributes'].map(k=>lookup(record,state.mapping[k]));
  const arrays=values.map(v=>v===undefined?[]:Array.isArray(v)?v:[v]);
  const length=Math.max(...arrays.map(v=>v.length));
  if(!length && Array.isArray(record.events)) return record.events.map(event=>({stamp:timestamp(event.timeUnixNano ?? event.timestamp ?? event.time),rawTime:event.timeUnixNano ?? event.timestamp ?? event.time,name:event.name,attributes:event.attributes}));
  return Array.from({length},(_,i)=>({stamp:timestamp(arrays[0][i]),rawTime:arrays[0][i],name:arrays[1][i],attributes:arrays[2][i]}));
}
async function selectSpan(item) {
  const token=++state.detailToken;state.activeRecord=item.record_id;
  markSelected('spans',String(item.record_id));$('detail-title').textContent='Span 详情';
  $('detail').classList.add('mobile-open');$('detail-content').replaceChildren(el('div','trace-loading','正在读取完整记录…'));
  try {
    const data=await api('/api/record',{scan_id:state.scan.scan_id,id:String(item.record_id)});
    if(token!==state.detailToken)return;
    state.detailData={item,data};renderDetails();$('detail-content').scrollTop=0;
  }catch(error){if(token===state.detailToken)empty($('detail-content'),'记录读取失败',error.message);}
}
function attributeBlock(label,value,open=false) { const details=el('details','attribute-block'); details.open=open; details.append(el('summary','',label),el('pre','',text(value))); return details; }
function renderSdkNote(resource) {
  const sdk=Otel.sdk(resource);
  if(!sdk)return null;
  const note=el('div','sdk-note');
  note.append(el('div','',`SDK：${sdk.name===undefined?'—':text(sdk.name)} · 版本：${sdk.version===undefined?'—':text(sdk.version)}`),
    el('div','',`SDK 语言：${sdk.languageLabel===undefined?'—':text(sdk.languageLabel)}`));
  return note;
}
function renderOperationContent(operation,record) {
  if(!operation || !['user','assistant','tool'].includes(operation.kind))return null;
  const attrs=Otel.attributes(lookup(record,state.mapping.attributes) ?? record.spanAttributes);
  const box=el('section',`operation-content content-${operation.kind}`);
  if(operation.kind==='tool') {
    const name=Otel.semantic(attrs,'tool.name');
    box.append(el('div','operation-content-label','工具名称'),el('div','tool-name',name===undefined?'未提供 tool.name':text(name)));
  } else {
    const content=Otel.semantic(attrs,'content');
    box.append(el('div','operation-content-label',operation.label),
      el('pre',content===undefined?'content-missing':'',content===undefined?'该记录未提供 spanAttributes.content':text(content)));
  }
  return box;
}
function renderAttributeGroups(body,value,resource=false) {
  const groups=Otel.groups(value,resource);
  if(!groups.length) {
    body.append(attributeBlock(resource?'完整资源属性':'完整 Span 属性',value,true));
    return;
  }
  for(const group of groups) {
    const details=el('details','attribute-group'); details.open=true;
    details.append(el('summary','',`${group.name} · ${group.entries.length}`));
    for(const [key,value] of group.entries) {
      const row=el('div','semantic-attribute');
      row.append(el('code','',key));
      row.append(el(value && typeof value==='object'?'pre':'div','attribute-value',text(value)));
      details.append(row);
    }
    body.append(details);
  }
  body.append(attributeBlock(resource?'完整资源属性（原始结构）':'完整 Span 属性（原始结构）',value));
}
function renderDetails() {
  const {item,data}=state.detailData, record=data.record, rawJSON=data.raw_json || text(record);
  const resource=lookup(record,state.mapping.resource), attrs=Otel.attributes(resource);
  const status=Otel.status(lookup(record,state.mapping.status));
  const body=el('div','detail-body'), badges=el('div','span-badges');
  const operation=Otel.operation(item.name);
  if(operation)badges.append(el('span',`pill operation-pill operation-${operation.kind}`,operation.label));
  badges.append(el('span',`pill ${status.kind==='error'?'error-pill':status.kind!=='ok'?'neutral-pill':''}`,status.label));
  const kind=lookup(record,state.mapping.kind);
  if(kind!==undefined) badges.append(el('span','pill kind-pill',Otel.spanKind(kind,state.mapping.kind_encoding)));
  body.append(badges,el('h3','',item.name || '未命名操作'));
  const content=renderOperationContent(operation,record);if(content)body.append(content);
  const pairs=[['服务',item.service || Otel.semantic(attrs,'service.name') || '—'],
    ['服务版本',Otel.semantic(attrs,'service.version')],['服务实例',Otel.semantic(attrs,'service.instance.id')],
    ['主机',item.host || Otel.semantic(attrs,'host.name') || '—'],['用户',Otel.semantic(attrs,'user.id') ?? Otel.semantic(attrs,'enduser.id')],
    ['会话 ID',state.thread?.label],['Trace ID',item.trace_id || '未提供'],['Span ID',item.span_id || '未提供'],['父 Span',item.parent_id || '根节点 / 未提供'],
    ['开始时间',date(item.stamp,true)],['耗时',duration(item.duration)],['原始副本',`${data.total} 条`]];
  const meta=el('dl','detail-meta');
  for(const [key,value] of pairs) if(value!==undefined) meta.append(el('dt','',key),el('dd',key.includes('ID') || key==='父 Span'?'mono':'',typeof value==='string'?value:text(value)));
  body.append(meta);
  const sdk=renderSdkNote(resource);if(sdk)body.append(sdk);
  const message=lookup(record,state.mapping.status_message);
  if(message!==undefined && message!=='') body.append(el('div',status.kind==='error'?'span-status-message error-message':'span-status-message',text(message)));
  const tabs=el('div','detail-tabs');
  for(const [key,label] of [['attributes','属性'],['events','事件'],['raw','原始记录']]) {
    const button=el('button',state.detailTab===key?'active':'',label);
    button.addEventListener('click',()=>{state.detailTab=key;renderDetails();});tabs.append(button);
  }
  body.append(tabs);
  if(state.detailTab==='attributes') {
    if(resource!==undefined) {body.append(el('h4','detail-section-title','RESOURCE · 资源信息'));renderAttributeGroups(body,resource,true);}
    const spanAttrs=lookup(record,state.mapping.attributes);
    if(spanAttrs!==undefined) {body.append(el('h4','detail-section-title','SPAN · 操作属性'));renderAttributeGroups(body,spanAttrs);}
    if(resource===undefined && spanAttrs===undefined) body.append(el('p','note','尚未找到映射的资源 / Span 属性。请在字段映射中指定路径，或查看完整原始记录。'));
    const technical={}; for(const key of ['spanKind','statusCode','statusMessage','serviceName','fullServiceName','ts','traceState','instrumentationScope','instrumentationLibrary','scope','links','droppedAttributesCount','droppedEventsCount','droppedLinksCount']) if(record[key]!==undefined) technical[key]=record[key];
    body.append(attributeBlock('采集与链路元数据（原始值）',technical));
  } else if(state.detailTab==='events') {
    const list=events(record);
    body.append(el('p','note',`${list.length} 个事件。平铺 events 字段按位置配对；也支持记录内的 events 对象数组，完整原始内容均保留。`));
    if(list.length) {
      const timeline=el('div','event-timeline');
      if(item.stamp!==null && item.duration>0) for(const event of list) {
        if(event.stamp===null) continue; const position=(event.stamp-item.stamp)/item.duration*100;
        if(position<0 || position>100) continue;
        const marker=el('i','event-marker'); marker.style.left=Math.min(position,99)+'%';marker.title=text(event.name ?? '事件');timeline.append(marker);
      }
      body.append(timeline);
      for(const [index,event] of list.entries()) {
        const attrs=Otel.attributes(event.attributes), exception=event.name==='exception' || Otel.semantic(attrs,'exception.type')!==undefined || Otel.semantic(attrs,'exception.message')!==undefined;
        const box=el('section',`event-item${exception?' exception-event':''}`);
        box.append(el('h4','',`${exception?'异常事件 · ':''}${text(event.name ?? `事件 ${index+1}`)}`),el('time','',event.stamp===null?'时间未识别':date(event.stamp,true)));
        if(exception) for(const [key,label] of [['exception.type','异常类型'],['exception.message','异常信息'],['exception.stacktrace','调用堆栈']]) {
          const value=Otel.semantic(attrs,key);if(value!==undefined) box.append(el('div','detail-section-title',label),el('pre','exception-value',text(value)));
        }
        if(event.rawTime!==undefined) box.append(el('div','note','原始时间：'+text(event.rawTime)));
        if(event.attributes!==undefined) box.append(attributeBlock('完整事件属性',event.attributes,!exception));
        body.append(box);
      }
    }
    for(const [key,label] of [['event_time','原始事件时间'],['event_name','原始事件名称'],['event_attributes','原始事件属性']]) {
      const value=lookup(record,state.mapping[key]);if(value!==undefined) body.append(attributeBlock(label,value));
    }
    if(record.events!==undefined) body.append(attributeBlock('原始 events',record.events));
  } else {
    const actions=el('div','detail-json-actions'), copy=el('button','button','复制 JSON');
    copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(rawJSON);copy.textContent='已复制';}catch{copy.textContent='复制失败，请手动选择文本';}});
    const download=el('button','button','导出记录');
    download.addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([rawJSON],{type:'application/json'}));const a=el('a');a.href=url;a.download=`span-${item.record_id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    actions.append(copy,download);body.append(actions,el('pre','raw-json',rawJSON));
  }
  body.append(el('h4','detail-section-title','来源与相同记录'));
  const sources=el('details','attribute-block');sources.append(el('summary','',`${data.total} 个完整内容相同的副本 · 点击查看来源`),el('pre','',text(data.copies || [])));
  if(data.has_more) sources.append(el('p','note','仅列出前 200 个来源位置。'));
  body.append(sources);$('detail-content').replaceChildren(body);
}
function openMappings() { if(!state.scan) return; renderMappingFields(state.mapping); $('mapping-dialog').showModal(); }
function renderMappingFields(mapping) { const container=$('mapping-fields');container.replaceChildren(); for(const [key,label,hint] of MAPPINGS) { const wrapper=el('label','',label);const select=pathSelect(mapping[key]);select.id='map-'+key;wrapper.append(select,el('small','',hint));container.append(wrapper); } $('kind-encoding').value=mapping.kind_encoding || 'auto'; $('time-unit').value=mapping.time_unit || 'auto';$('duration-unit').value=mapping.duration_unit || 'ns'; }
let draftRules=[];
function renderRules() { const container=$('filter-rules');container.replaceChildren(); draftRules.forEach((rule,index)=>{const row=el('div','filter-rule');const select=pathSelect(rule.path,false);select.setAttribute('aria-label',`条件 ${index+1} 字段`);select.addEventListener('change',()=>rule.path=select.value?JSON.parse(select.value):null); const op=el('select');for(const [key,label] of [['contains','包含'],['equals','等于'],['exists','字段存在'],['not_exists','字段不存在'],['between','范围']])op.append(new Option(label,key));op.value=rule.operator;op.setAttribute('aria-label',`条件 ${index+1} 运算`);op.addEventListener('change',()=>{rule.operator=op.value;renderRules();}); const values=el('div','range-inputs'); if(!['exists','not_exists'].includes(rule.operator)) for(const key of rule.operator==='between'?['from','to']:['value']) {const input=el('input');input.value=rule[key] || '';input.placeholder=key==='from'?'起点':key==='to'?'终点':'关键词';input.setAttribute('aria-label',`条件 ${index+1} ${input.placeholder}`);input.addEventListener('input',()=>rule[key]=input.value);values.append(input);}const remove=el('button','icon-button','×');remove.type='button';remove.setAttribute('aria-label',`删除条件 ${index+1}`);remove.addEventListener('click',()=>{draftRules.splice(index,1);renderRules();});row.append(select,op,values,remove);container.append(row);}); }
function openFilters() { if(!state.scan)return;draftRules=structuredClone(state.rules);for(const prefix of ['from','to'])for(const [i,unit] of ['day','hour','minute'].entries())$(prefix+'-'+unit).value=state.range['time_'+prefix]?.[i]??'';$('filter-error').textContent='';renderRules();$('filter-dialog').showModal(); }
function readTime(prefix) {const values=['day','hour','minute'].map(unit=>$(prefix+'-'+unit).value);if(values.every(v=>v===''))return null;if(values.some(v=>v===''))throw new Error('每个时间端点请完整填写日、时、分，或全部留空。');const numbers=values.map(Number);if(numbers.some((v,i)=>!Number.isInteger(v)||v<(i===0?1:0)||v>[31,23,59][i]))throw new Error('目录时间超出有效范围。');return numbers;}
async function demo() {if(state.demoRoot)await scan(state.demoRoot,true);else notice('示例目录不可用，请重启最新版本服务。',true);}
$('source-open').addEventListener('click',()=>$('source-dialog').showModal());$('source-label').addEventListener('click',()=>$('source-dialog').showModal());$('source-form').addEventListener('submit',e=>{e.preventDefault();scan($('root-path').value.trim());});for(const id of ['demo-button','source-demo'])$(id).addEventListener('click',demo);document.querySelectorAll('.close-dialog').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));$('mapping-open').addEventListener('click',openMappings);$('mapping-reset').addEventListener('click',()=>renderMappingFields(guessedMapping()));$('mapping-form').addEventListener('submit',e=>{e.preventDefault();for(const [key] of MAPPINGS)state.mapping[key]=$('map-'+key).value?JSON.parse($('map-'+key).value):null;state.mapping.kind_encoding=$('kind-encoding').value; state.mapping.time_unit=$('time-unit').value;state.mapping.duration_unit=$('duration-unit').value;state.saved=null;$('mapping-dialog').close();query();});$('filters-open').addEventListener('click',openFilters);$('add-rule').addEventListener('click',()=>{draftRules.push({path:null,operator:'contains',value:''});renderRules();});$('filter-reset').addEventListener('click',()=>{draftRules=[];for(const prefix of ['from','to'])for(const unit of ['day','hour','minute'])$(prefix+'-'+unit).value='';renderRules();});$('filter-form').addEventListener('submit',e=>{e.preventDefault();try{const from=readTime('from'),to=readTime('to');if(from && to && from[0]*1440+from[1]*60+from[2]>to[0]*1440+to[1]*60+to[2])throw new Error('时间起点不能晚于终点。');for(const rule of draftRules){if(!rule.path)throw new Error('请为每个条件选择字段。');if(['contains','equals'].includes(rule.operator) && !rule.value)throw new Error('请填写筛选关键词。');if(rule.operator==='between' && !rule.from && !rule.to)throw new Error('范围条件至少填写一个端点。');}state.rules=structuredClone(draftRules);state.range={time_from:from,time_to:to};$('filter-count').textContent=state.rules.length+(from||to?1:0);state.saved=null;$('filter-dialog').close();query();}catch(error){$('filter-error').textContent=error.message;}});
$('multi-only').addEventListener('change',()=>query());$('trace-sort').addEventListener('change',()=>query());$('deduplicate').addEventListener('change',()=>query());$('refresh').addEventListener('click',()=>query(state.page));
for(const level of ['users','sessions','traces','spans'])for(const [side,delta] of [['prev',-1],['next',1]])$(level+'-'+side).addEventListener('click',()=>pageColumn(level,delta));
let searchTimer;$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>query(),400);});$('search').addEventListener('keydown',e=>{if(e.key==='Enter'){clearTimeout(searchTimer);query();}});
$('detail-close').addEventListener('click',()=>$('detail').classList.remove('mobile-open'));$('detail-open').addEventListener('click',()=>$('detail').classList.add('mobile-open'));

(async()=>{busy(true);try{try{state.saved=JSON.parse(localStorage.getItem('officeace-trace-v2'));}catch{}const status=await api('/api/status');if(!status.capabilities?.includes('hierarchy_browser'))throw new Error('后端版本较旧，请停止原服务并重新运行 start.command / start.sh。');state.demoRoot=status.trace_demo_root;$('root-path').value=state.saved?.root || status.default_root || '';notice('连接本机数据目录，或载入示例开始探索。');}catch(error){notice(error.message,true);}finally{busy(false);}})();
