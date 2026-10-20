'use strict';
const $ = (id) => document.getElementById(id);
const state = { scan: null, mapping: {}, fields: [], rules: [], range: {}, page: 1, pages: 0,
  selected: null, spans: [], waterfall: null, collapsed: new Set(), spanPage: 1, busy: false,
  traceToken: 0, detailToken: 0, detailTab: 'attributes', activeRecord: null, saved: null, demo: false };
const COLORS = ['#508a81','#829d72','#a89c68','#7e91a9','#ab8b9c','#b48f6a','#6d9caa','#8b8fa7'];
const MAPPINGS = [
  ['trace','Trace ID','将同一次链路聚合在一起',['traceId','traceID','trace_id']],
  ['span','Span ID','当前调用自身的唯一标识',['spanId','spanID','span_id']],
  ['parent','父 Span ID','用于还原父子调用关系',['parentSpanID','parentSpanId','parent_span_id']],
  ['name','操作名称','显示在时间轴每一行',['spanName','name']],
  ['start','开始时间','时间轴定位依据',['start','startTime','start_time']],
  ['duration','持续时间','横条长度；请确认下方单位',['duration']],
  ['service','服务名称','按服务分配颜色',['serviceName','fullServiceName','service.name']],
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
function busy(value) { state.busy = value; document.body.classList.toggle('busy', value); ['scan-button','source-demo','demo-button','empty-demo','refresh','mapping-open','filters-open','deduplicate','multi-only','trace-sort','search','prev','next','source-open'].forEach(id => { const n = $(id); if(n) n.disabled = value || (['mapping-open','filters-open','deduplicate','multi-only','trace-sort','refresh','search'].includes(id) && !state.scan); }); if (!value) { $('prev').disabled = state.page <= 1; $('next').disabled = state.page >= state.pages; } }
function payload() { return {scan_id:state.scan.scan_id,mapping:state.mapping,field_filters:state.rules,deduplicate:$('deduplicate').checked,min_spans:$('multi-only').checked?2:1,sort:$('trace-sort').value,...state.range}; }
function save() { if (state.demo) return; try { localStorage.setItem('officeace-trace-v2',JSON.stringify({root:$('root-path').value,mapping:state.mapping,rules:state.rules,range:state.range,dedupe:$('deduplicate').checked,multiOnly:$('multi-only').checked,sort:$('trace-sort').value})); } catch {} }
function pathSelect(value, optional = true) { const select = el('select'); select.append(new Option(optional ? '未映射 / 不使用' : '选择字段', '')); for(const f of state.fields) { const path = JSON.stringify(f.path); select.append(new Option(prettyPath(f.path),path)); } select.value = value ? JSON.stringify(value) : ''; select.title = value ? prettyPath(value) : ''; select.addEventListener('change',()=>{ select.title = select.value ? prettyPath(JSON.parse(select.value)) : ''; }); return select; }
function guessedMapping() { const mapping = {}; for (const [key,, , aliases] of MAPPINGS) { let found; if(key==='host') found = state.fields.find(f=>JSON.stringify(f.path)===JSON.stringify(['resourceAttributes','host.name'])); if(!found) for(const alias of aliases) { found=state.fields.find(f=>f.path.length===1 && f.path[0]===alias); if(found) break; } if(!found) for(const alias of aliases) { found=state.fields.find(f=>f.path.at(-1)===alias); if(found) break; } if(!found && key==='host') found=state.fields.find(f=>f.path.slice(-2).join('.')==='host.name'); mapping[key] = found?.path || null; } if(!mapping.status) mapping.status=state.fields.find(f=>JSON.stringify(f.path)===JSON.stringify(['status','code']))?.path || null; if(!mapping.status_message) mapping.status_message=state.fields.find(f=>JSON.stringify(f.path)===JSON.stringify(['status','message']))?.path || null; mapping.kind_encoding='auto'; mapping.time_unit = 'auto'; mapping.duration_unit = 'ns'; return mapping; }
function validPath(path) { return path && state.fields.some(f=>JSON.stringify(f.path)===JSON.stringify(path)); }
async function scan(root, demo = false) { if (state.busy) return; busy(true); $('source-error').textContent=''; notice('正在扫描目录并建立索引，数据量较大时请稍候…'); state.traceToken++; state.detailToken++; try { const result = await api('/api/scan',{root}); state.scan = result; state.demo = demo; state.fields = result.fields; state.mapping = guessedMapping(); state.rules = []; state.range={}; $('deduplicate').checked=true; $('multi-only').checked=false; $('trace-sort').value='recent';
    if (!demo && state.saved) { const saved=state.saved; $('multi-only').checked=saved.multiOnly===true; if(['recent','spans_desc','duration_desc'].includes(saved.sort)) $('trace-sort').value=saved.sort; if(['auto','js','otlp'].includes(saved.mapping?.kind_encoding)) state.mapping.kind_encoding=saved.mapping.kind_encoding; for (const [key] of MAPPINGS) if(validPath(saved.mapping?.[key])) state.mapping[key]=saved.mapping[key]; if(['auto','s','ms','us','ns'].includes(saved.mapping?.time_unit)) state.mapping.time_unit=saved.mapping.time_unit; if(['s','ms','us','ns'].includes(saved.mapping?.duration_unit)) state.mapping.duration_unit=saved.mapping.duration_unit; state.rules=(saved.rules || []).filter(r=>validPath(r.path)); state.range=saved.range || {}; $('deduplicate').checked=saved.dedupe!==false; }
    $('root-path').value=result.root; $('source-label').textContent=(demo?'示例数据 / ':'') + result.root; $('source-label').title=result.root; $('scan-info').textContent=`${number(result.record_count)} 条记录 · ${number(result.file_count)} 个文件`; $('source-dialog').close(); $('filter-count').textContent=state.rules.length+(state.range.time_from || state.range.time_to ? 1 : 0); clearTrace(); await query(1,true); if(result.warnings?.length) notice(`已连接。扫描提示：${result.warnings.slice(0,3).join('；')}`,true); save();
  } catch(error) { $('source-error').textContent=error.message; notice(error.message,true); } finally { busy(false); } }
function empty(container,title,description) { const box=el('div','empty compact'); box.append(el('span','empty-symbol','⌕'),el('h3','',title),el('p','',description)); container.replaceChildren(box); }
async function query(page=1,alreadyBusy=false) { if(!state.scan || (state.busy && !alreadyBusy)) return; if(!alreadyBusy) busy(true); notice('正在匹配记录并聚合链路；首次查询会建立字段索引…'); state.traceToken++; state.detailToken++; try { const result=await api('/api/traces',{...payload(),page,page_size:30,search:$('search').value.trim()}); state.page=result.page; state.pages=result.pages; $('metric-traces').textContent=number(result.stats.traces); $('metric-spans').textContent=number(result.stats.spans); $('metric-errors').textContent=number(result.stats.errors); $('metric-duration').textContent=duration(result.stats.longest); $('list-count').textContent=number(result.total); $('list-page').textContent=`${result.page} / ${result.pages || 1}`; renderTraceList(result.items); renderDiagnostics(result); $('list-sort-label').textContent=$('trace-sort').selectedOptions[0].textContent+' ↓'; notice(`找到 ${number(result.total)} 条链路 · 命中 ${number(result.stats.spans)} 条 Span${$('deduplicate').checked ? ` · 已过滤 ${number(result.stats.duplicates)} 条相同记录` : ''} · ${result.cache_hit?'复用缓存':'首次聚合'} ${result.elapsed_ms} ms`); save(); clearTrace(); if(result.items.length) await selectTrace(result.items[0]); } catch(error) { notice(error.message,true); } finally { if(!alreadyBusy) busy(false); } }
function renderDiagnostics(result) {
  const diagnostics=result.diagnostics || {}, bar=$('trace-diagnostics');
  const path=state.mapping.trace?prettyPath(state.mapping.trace):'未映射 Trace ID';
  const segments=[`按 ${path} 分组`,`单节点 ${number(diagnostics.single_traces)} 条（其中 dns.lookup ${number(diagnostics.dns_single_traces)} 条）`];
  if(diagnostics.missing_trace_id) segments.push(`${number(diagnostics.missing_trace_id)} 条记录缺少 Trace ID`);
  if(diagnostics.missing_span_id) segments.push(`${number(diagnostics.missing_span_id)} 条缺少 Span ID，层级可能不完整`);
  if(state.mapping.trace && JSON.stringify(state.mapping.trace)===JSON.stringify(state.mapping.span)) segments.push('注意：Trace ID 与 Span ID 映射到了同一字段，请检查映射');
  if(diagnostics.dns_single_traces) segments.push('不同 Trace ID 的 DNS 记录分别展示；可勾选「只看多节点链路」查找其他链路');
  if($('multi-only').checked) segments.push('当前只显示命中记录数 ≥ 2 的链路；清空其他字段条件可扩大范围');
  bar.textContent=segments.join(' · ');bar.hidden=false;
}
function renderTraceList(items) { const list=$('trace-list'); list.replaceChildren(); if(!items.length) { empty(list,'没有匹配的链路',$('multi-only').checked?'当前没有多节点链路；取消此选项，或放宽字段和时间条件。':'调整筛选条件或目录时间范围'); return; } for(const item of items) { const card=el('button','trace-card'); card.dataset.key=item.trace_key; const top=el('div','trace-card-top'); const name=el('strong','',item.name); name.title=item.name; top.append(name,el('span',item.errors?'pill error-pill':'pill',item.errors?`${item.errors} 异常`:'无异常标记')); const meta=el('div','trace-card-meta'); meta.append(el('span','',`${number(item.count)} spans`),el('span','',`${item.services} 服务`),el('span','duration',duration(item.first===null?null:item.last-item.first))); const bottom=el('div','trace-card-bottom'); bottom.append(el('span','',date(item.first,true)),el('span','',item.trace_id?'':'缺少 Trace ID')); card.append(top,el('span','mono',item.trace_id || '独立记录'),meta,bottom); card.addEventListener('click',()=>selectTrace(item)); list.append(card); } }
function clearTrace() { state.selected=null; state.spans=[]; state.waterfall=null; state.activeRecord=null; state.traceToken++; state.detailToken++; $('trace-title').textContent='链路时间瀑布图'; $('trace-id').textContent='选择左侧链路，查看执行过程'; ['trace-summary','trace-warnings','waterfall-tools','more-spans','expand-all'].forEach(id=>$(id).hidden=true); $('span-count').textContent='尚未选择链路'; empty($('waterfall'),'选择一条链路','按时间查看调用分布和执行层级'); empty($('detail-content'),'深入一个 Span','选择调用，查看属性与事件'); $('detail').classList.remove('mobile-open'); }
async function selectTrace(item) { const token=++state.traceToken; state.detailToken++; state.selected=item; $('detail').classList.remove('mobile-open'); state.collapsed.clear(); state.spans=[]; state.spanPage=1; $('zoom').value='1'; $('waterfall').style.width='100%'; $('trace-title').textContent=item.name; $('trace-id').textContent=item.trace_id || '缺少 Trace ID · 此记录单独展示'; $('trace-summary').hidden=true; $('trace-warnings').hidden=true; $('more-spans').hidden=true; $('expand-all').hidden=true; $('waterfall').replaceChildren(el('div','trace-loading','正在还原链路时间轴…')); $('detail-content').replaceChildren(); empty($('detail-content'),'选择一个 Span','查看属性、事件和原始记录'); for(const n of $('trace-list').children) { const on=n.dataset.key===item.trace_key; n.classList.toggle('selected',on); if(n.tagName==='BUTTON') n.setAttribute('aria-pressed',String(on)); } try { const result=await api('/api/trace/spans',{...payload(),trace_key:item.trace_key,page:1,page_size:200}); if(token!==state.traceToken) return; state.spans=result.items; state.waterfall=result; renderWaterfall(); if(result.items.length) selectSpan(result.items[0],false); } catch(error) { if(token===state.traceToken) { empty($('waterfall'),'链路加载失败',error.message); notice(error.message,true); } } }
function serviceColor(name) { const services=state.waterfall?.services || []; return COLORS[Math.max(0,services.indexOf(name)) % COLORS.length]; }
function renderWaterfall() { const result=state.waterfall; if(!result) return; const summary=$('trace-summary'); summary.replaceChildren(); for(const [label,value] of [['耗时',duration(result.first===null?null:result.last-result.first)],['Span',number(result.total)],['服务',number(result.services.length)],['异常',number(result.errors)]]) { const n=el('span'); n.append(el('span','',label+' '),el('b','',value)); summary.append(n); } summary.hidden=false;
  const warnings=[]; if(result.missing_span_id) warnings.push(`${number(result.missing_span_id)} 条缺少 Span ID，无法完整关联层级`); if(result.missing_parent) warnings.push(`${number(result.missing_parent)} 条父节点未找到或标识不唯一`); if(result.duplicate_span_ids) warnings.push(`${number(result.duplicate_span_ids)} 个 Span ID 重复，相关父关系未连接`); if(result.cycles) warnings.push('检测到循环父子关系，已断开显示'); if(result.missing_time) warnings.push(`${number(result.missing_time)} 条开始时间未识别`); if(result.missing_duration) warnings.push(`${number(result.missing_duration)} 条耗时未识别，请检查字段及单位`); $('trace-warnings').textContent=warnings.join('；'); $('trace-warnings').hidden=!warnings.length;
  const legend=$('service-legend'); legend.replaceChildren(); for(const s of result.services) { const n=el('span'); const dot=el('i','legend-dot'); dot.style.background=serviceColor(s); n.append(dot,document.createTextNode(s)); n.title=s; legend.append(n); } $('waterfall-tools').hidden=false; $('expand-all').hidden=false;
  const waterfall=$('waterfall'); waterfall.replaceChildren(); const axis=el('div','axis-row'); axis.append(el('div','axis-name','操作 / 调用层级')); const ticks=el('div','axis-ticks'); const total=result.first===null?0:result.last-result.first; for(let i=0;i<5;i++) ticks.append(el('span','',duration(total*i/4))); axis.append(ticks); waterfall.append(axis);
  let hiddenDepth=null; let visible=0; for(const item of state.spans) { if(hiddenDepth!==null) { if(item.depth>hiddenDepth) continue; hiddenDepth=null; } if(state.collapsed.has(item.record_id)) hiddenDepth=item.depth; const row=el('div',`span-row${item.error?' is-error':''}${state.activeRecord===item.record_id?' active':''}`); row.dataset.record=String(item.record_id); row.tabIndex=0; row.setAttribute('role','button'); row.setAttribute('aria-label',`${item.name || '未命名操作'}，耗时 ${duration(item.duration)}，层级 ${item.depth}`); row.setAttribute('aria-pressed',String(state.activeRecord===item.record_id)); const label=el('div','span-label'); label.style.paddingLeft=(8+Math.min(item.depth,12)*12)+'px'; const toggle=el('button','tree-toggle',item.has_children?(state.collapsed.has(item.record_id)?'▸':'▾'):'·'); toggle.disabled=!item.has_children; toggle.setAttribute('aria-label',state.collapsed.has(item.record_id)?'展开子调用':'收起子调用'); if(item.has_children) toggle.setAttribute('aria-expanded',String(!state.collapsed.has(item.record_id))); toggle.addEventListener('click',e=>{e.stopPropagation(); state.collapsed.has(item.record_id)?state.collapsed.delete(item.record_id):state.collapsed.add(item.record_id); renderWaterfall();}); const dot=el('i','span-service-dot'); dot.style.background=serviceColor(item.service); const name=el('span','name',item.name || '未命名操作'); name.title=`${item.name || ''}\n${item.service || '未提供服务'}\n层级 ${item.depth}`; label.append(toggle,dot,name);
    const track=el('div','span-track'); if(item.stamp!==null) { const start=total>0?Math.max(0,Math.min(100,(item.stamp-result.first)/total*100)):0; const width=total>0?Math.max(0,Math.min(100-start,(item.duration || 0)/total*100)):0; const bar=el('div','span-bar'); bar.style.left=start+'%'; bar.style.width=width+'%'; bar.style.background=serviceColor(item.service); bar.title=`${item.name || ''} · ${duration(item.duration)} · ${date(item.stamp,true)}`; const caption=el('span','bar-caption',duration(item.duration)); if(width>22) { caption.classList.add('inside'); bar.append(caption); } else { if(start+width>75) { caption.style.right=(100-start+.8)+'%'; caption.style.left='auto'; } else caption.style.left=(start+width+1)+'%'; track.append(caption); } track.append(bar); } else track.append(el('span','missing-timing','开始时间未识别')); row.append(label,track); row.addEventListener('click',()=>selectSpan(item)); row.addEventListener('keydown',e=>{if(e.target===row && ['Enter',' '].includes(e.key)){e.preventDefault(); selectSpan(item);}}); waterfall.append(row); visible++; }
  $('more-spans').hidden=result.page>=result.pages; $('more-spans').textContent=`加载更多 Span · 已载入 ${number(state.spans.length)} / ${number(result.total)}`; $('span-count').textContent=`显示 ${number(visible)} / ${number(result.total)} Span`; if(!result.total) empty(waterfall,'没有可展示的 Span','请调整目录时间范围'); }
function lookup(record,path) { if(!path) return undefined; function visit(value,index) { if(index===path.length) return value; if(value===null || typeof value!=='object') return undefined; if(path[index]==='[]') { if(!Array.isArray(value)) return undefined; return value.map(x=>visit(x,index+1)).filter(x=>x!==undefined); } return Object.prototype.hasOwnProperty.call(value,path[index])?visit(value[path[index]],index+1):undefined; } return visit(record,0); }
function timestamp(value) { if(Array.isArray(value)) return Otel.hrtime(value); if(value===null || value===undefined || typeof value==='boolean') return null; const v=Number(value); if(typeof value==='number' || (typeof value==='string' && value.trim()!=='' && Number.isFinite(v))) { const unit=state.mapping.time_unit || 'auto'; const divisor={s:1,ms:1e3,us:1e6,ns:1e9}[unit] || (Math.abs(v)>=1e17?1e9:Math.abs(v)>=1e14?1e6:Math.abs(v)>=1e11?1e3:1); return Number.isFinite(v)?v/divisor:null; } const n=Date.parse(value); return Number.isNaN(n)?null:n/1000; }
function events(record) {
  const values=['event_time','event_name','event_attributes'].map(k=>lookup(record,state.mapping[k]));
  const arrays=values.map(v=>v===undefined?[]:Array.isArray(v)?v:[v]);
  const length=Math.max(...arrays.map(v=>v.length));
  if(!length && Array.isArray(record.events)) return record.events.map(event=>({stamp:timestamp(event.timeUnixNano ?? event.timestamp ?? event.time),rawTime:event.timeUnixNano ?? event.timestamp ?? event.time,name:event.name,attributes:event.attributes}));
  return Array.from({length},(_,i)=>({stamp:timestamp(arrays[0][i]),rawTime:arrays[0][i],name:arrays[1][i],attributes:arrays[2][i]}));
}
async function selectSpan(item,open=true) { const token=++state.detailToken; state.activeRecord=item.record_id; for(const row of $('waterfall').querySelectorAll('.span-row')) { const active=row.dataset.record===String(item.record_id); row.classList.toggle('active',active); row.setAttribute('aria-pressed',String(active)); } if(open) $('detail').classList.add('mobile-open'); $('detail-content').replaceChildren(el('div','trace-loading','正在读取完整记录…')); try { const data=await api('/api/record',{scan_id:state.scan.scan_id,id:String(item.record_id)}); if(token!==state.detailToken) return; state.detailData={item,data}; renderDetails(); $('detail-content').scrollTop=0; addEventMarkers(item,data.record); } catch(error) { if(token===state.detailToken) empty($('detail-content'),'记录读取失败',error.message); } }
function addEventMarkers(item,record) { $('waterfall').querySelectorAll('.span-track .event-marker').forEach(n=>n.remove()); const track=[...$('waterfall').querySelectorAll('.span-row')].find(n=>n.dataset.record===String(item.record_id))?.querySelector('.span-track'); const result=state.waterfall; if(!track || result.first===null || result.last<=result.first) return; for(const event of events(record)) { if(event.stamp===null || event.stamp<result.first || event.stamp>result.last) continue; const marker=el('i','event-marker'); marker.style.left=((event.stamp-result.first)/(result.last-result.first)*100)+'%'; marker.title=`${text(event.name ?? '事件')} · ${date(event.stamp,true)}`; track.append(marker); } }
function attributeBlock(label,value,open=false) { const details=el('details','attribute-block'); details.open=open; details.append(el('summary','',label),el('pre','',text(value))); return details; }
function renderSdkCard(resource) {
  const sdk=Otel.sdk(resource);
  if(!sdk) return null;
  const card=el('section','sdk-card');
  card.append(el('div','sdk-heading','OTel 资源标识'));
  const title=el('strong','',sdk.languageLabel === undefined ? '遥测 SDK' : text(sdk.languageLabel));
  card.append(title);
  const meta=el('div','sdk-meta');
  if(sdk.name!==undefined) meta.append(el('span','',`SDK ${text(sdk.name)}`));
  if(sdk.version!==undefined) meta.append(el('span','',`SDK 版本 ${text(sdk.version)}`));
  card.append(meta);
  card.title='依据 telemetry.sdk.* 资源属性展示；不能由此确定 exporter 或 Span 类型编码。';
  return card;
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
  badges.append(el('span',`pill ${status.kind==='error'?'error-pill':status.kind!=='ok'?'neutral-pill':''}`,status.label));
  const kind=lookup(record,state.mapping.kind);
  if(kind!==undefined) badges.append(el('span','pill kind-pill',Otel.spanKind(kind,state.mapping.kind_encoding)));
  body.append(badges,el('h3','',item.name || '未命名操作'));
  const sdk=renderSdkCard(resource); if(sdk) body.append(sdk);
  const pairs=[['服务',item.service || Otel.semantic(attrs,'service.name') || '—'],
    ['服务版本',Otel.semantic(attrs,'service.version')],['服务实例',Otel.semantic(attrs,'service.instance.id')],
    ['主机',item.host || Otel.semantic(attrs,'host.name') || '—'],['用户',Otel.semantic(attrs,'user.id') ?? Otel.semantic(attrs,'enduser.id')],
    ['Span ID',item.span_id || '未提供'],['父 Span',item.parent_id || '根节点 / 未提供'],
    ['开始时间',date(item.stamp,true)],['耗时',duration(item.duration)],['原始副本',`${data.total} 条`]];
  const meta=el('dl','detail-meta');
  for(const [key,value] of pairs) if(value!==undefined) meta.append(el('dt','',key),el('dd',key.includes('ID') || key==='父 Span'?'mono':'',typeof value==='string'?value:text(value)));
  body.append(meta);
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
$('source-open').addEventListener('click',()=>$('source-dialog').showModal());$('source-label').addEventListener('click',()=>$('source-dialog').showModal());$('source-form').addEventListener('submit',e=>{e.preventDefault();scan($('root-path').value.trim());});for(const id of ['demo-button','empty-demo','source-demo'])$(id).addEventListener('click',demo);document.querySelectorAll('.close-dialog').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));$('mapping-open').addEventListener('click',openMappings);$('mapping-reset').addEventListener('click',()=>renderMappingFields(guessedMapping()));$('mapping-form').addEventListener('submit',e=>{e.preventDefault();for(const [key] of MAPPINGS)state.mapping[key]=$('map-'+key).value?JSON.parse($('map-'+key).value):null;state.mapping.kind_encoding=$('kind-encoding').value; state.mapping.time_unit=$('time-unit').value;state.mapping.duration_unit=$('duration-unit').value;state.saved=null;$('mapping-dialog').close();query();});$('filters-open').addEventListener('click',openFilters);$('add-rule').addEventListener('click',()=>{draftRules.push({path:null,operator:'contains',value:''});renderRules();});$('filter-reset').addEventListener('click',()=>{draftRules=[];for(const prefix of ['from','to'])for(const unit of ['day','hour','minute'])$(prefix+'-'+unit).value='';renderRules();});$('filter-form').addEventListener('submit',e=>{e.preventDefault();try{const from=readTime('from'),to=readTime('to');if(from && to && from[0]*1440+from[1]*60+from[2]>to[0]*1440+to[1]*60+to[2])throw new Error('时间起点不能晚于终点。');for(const rule of draftRules){if(!rule.path)throw new Error('请为每个条件选择字段。');if(['contains','equals'].includes(rule.operator) && !rule.value)throw new Error('请填写筛选关键词。');if(rule.operator==='between' && !rule.from && !rule.to)throw new Error('范围条件至少填写一个端点。');}state.rules=structuredClone(draftRules);state.range={time_from:from,time_to:to};$('filter-count').textContent=state.rules.length+(from||to?1:0);state.saved=null;$('filter-dialog').close();query();}catch(error){$('filter-error').textContent=error.message;}});
$('multi-only').addEventListener('change',()=>query());$('trace-sort').addEventListener('change',()=>query());$('deduplicate').addEventListener('change',()=>query());$('refresh').addEventListener('click',()=>query(state.page));$('prev').addEventListener('click',()=>query(state.page-1));$('next').addEventListener('click',()=>query(state.page+1));let searchTimer;$('search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>query(),400);});$('search').addEventListener('keydown',e=>{if(e.key==='Enter'){clearTimeout(searchTimer);query();}});$('zoom').addEventListener('change',()=>$('waterfall').style.width=(Number($('zoom').value)*100)+'%');$('expand-all').addEventListener('click',()=>{state.collapsed.clear();renderWaterfall();});$('detail-close').addEventListener('click',()=>$('detail').classList.remove('mobile-open'));$('more-spans').addEventListener('click',async()=>{const token=state.traceToken;$('more-spans').disabled=true;try{const result=await api('/api/trace/spans',{...payload(),trace_key:state.selected.trace_key,page:state.waterfall.page+1,page_size:200});if(token!==state.traceToken)return;state.spans.push(...result.items);state.waterfall=result;renderWaterfall();}catch(error){notice(error.message,true);}finally{$('more-spans').disabled=false;}});
(async()=>{busy(true);try{try{state.saved=JSON.parse(localStorage.getItem('officeace-trace-v2'));}catch{}const status=await api('/api/status');if(!status.capabilities?.includes('traces') || !status.capabilities?.includes('otel_semantics') || !status.capabilities?.includes('trace_query_cache'))throw new Error('后端版本较旧，请停止原服务并重新运行 start.command / start.sh。');state.demoRoot=status.trace_demo_root;$('root-path').value=state.saved?.root || status.default_root || '';notice('连接本机数据目录，或载入示例开始探索。');}catch(error){notice(error.message,true);}finally{busy(false);}})();
