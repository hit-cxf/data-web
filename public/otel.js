'use strict';
// Display semantics only. Original records and keys remain available in the raw tab.
const Otel = (() => {
  function anyValue(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return value;
    const keys = Object.keys(value);
    if (keys.length !== 1) return value;
    const key = keys[0];
    if (['stringValue','boolValue','intValue','doubleValue','bytesValue'].includes(key)) return value[key];
    if (key === 'arrayValue' && Array.isArray(value[key]?.values)) return value[key].values.map(anyValue);
    if (key === 'kvlistValue' && Array.isArray(value[key]?.values)) return attributes(value[key].values);
    return value;
  }
  function attributes(value) {
    if (Array.isArray(value) && value.every(entry => entry && typeof entry.key === 'string' && Object.hasOwn(entry,'value'))) {
      // Duplicate keys are retained as an array instead of silently overwriting values.
      const result = Object.create(null), seen = new Set();
      for (const entry of value) {
        const v = anyValue(entry.value);
        if (seen.has(entry.key)) result[entry.key].push(v);
        else if (Object.hasOwn(result,entry.key)) { result[entry.key] = [result[entry.key],v]; seen.add(entry.key); }
        else result[entry.key] = v;
      }
      return result;
    }
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  }
  function semantic(attrs, key) {
    if (Object.hasOwn(attrs,key)) return anyValue(attrs[key]);
    let value = attrs;
    for (const token of key.split('.')) {
      if (!value || typeof value !== 'object' || !Object.hasOwn(value,token)) return undefined;
      value = value[token];
    }
    return anyValue(value);
  }
  function status(value) {
    if (value && typeof value === 'object') value = value.code;
    const key = String(value ?? '').trim().toUpperCase().replace(/^STATUS_CODE_|^STATUSCODE\./,'');
    if (['0','UNSET'].includes(key)) return {label:'UNSET · 未设置',kind:'unset'};
    if (['1','OK'].includes(key)) return {label:'OK · 成功',kind:'ok'};
    if (['2','ERROR'].includes(key)) return {label:'ERROR · 异常',kind:'error'};
    return {label: value == null ? '未提供状态' : `未知状态 · ${String(value)}`,kind:'unknown'};
  }
  function spanKind(value, encoding='auto') {
    if (value == null || value === '') return '未提供调用类型';
    const key=String(value).toUpperCase().replace(/^SPAN_KIND_|^SPANKIND\./,'');
    const labels={INTERNAL:'内部操作',SERVER:'服务端接收',CLIENT:'客户端调用',PRODUCER:'消息生产',CONSUMER:'消息消费',UNSPECIFIED:'未指定'};
    if (Object.hasOwn(labels,key)) return `${key} · ${labels[key]}`;
    if (!/^\d+$/.test(key)) return `未知类型 · ${String(value)}`;
    const kinds=encoding==='js' ? ['INTERNAL','SERVER','CLIENT','PRODUCER','CONSUMER'] : encoding==='otlp' ? ['UNSPECIFIED','INTERNAL','SERVER','CLIENT','PRODUCER','CONSUMER'] : [];
    const name=kinds[Number(key)];
    return name ? `${name} · ${labels[name]}` : `类型 ${key} · ${encoding==='auto'?'待确认编码':'未知值'}`;
  }
  const languages={nodejs:'Node.js',webjs:'浏览器 JavaScript',python:'Python',java:'Java',go:'Go',dotnet:'.NET',cpp:'C++',ruby:'Ruby',rust:'Rust',php:'PHP',swift:'Swift'};
  function sdk(resource) {
    const attrs=attributes(resource);
    const name=semantic(attrs,'telemetry.sdk.name'), language=semantic(attrs,'telemetry.sdk.language'), version=semantic(attrs,'telemetry.sdk.version');
    if (name===undefined && language===undefined && version===undefined) return null;
    return {name,language,version,languageLabel:languages[String(language)] || language};
  }
  const RESOURCE_GROUPS=[['服务与实例',/^(service\.|deployment\.)/],['主机与用户',/^(host\.|user\.|enduser\.)/],['运行环境',/^(process\.|os\.|container\.|k8s\.|cloud\.)/],['采集 SDK',/^(telemetry\.|otel\.)/]];
  const SPAN_GROUPS=[['异常信息',/^(exception\.|error\.)/],['HTTP 与 RPC',/^(http\.|url\.|server\.|client\.|network\.|rpc\.)/],['数据库',/^db\./],['AI 与工具',/^(gen_ai\.|llm\.|ai\.|tool[._])/],['输入与输出',/^(input|output|prompt|completion|messages?|response|request)([._]|$)/]];
  function groups(value, resource=false) {
    const attrs=attributes(value), definitions=resource?RESOURCE_GROUPS:SPAN_GROUPS;
    const buckets=definitions.map(([name])=>({name,entries:[]}));
    const other={name:resource?'其他资源属性':'其他 Span 属性',entries:[]};
    for (const [key,v] of Object.entries(attrs)) {
      const index=definitions.findIndex(([,pattern])=>pattern.test(key));
      (index<0?other:buckets[index]).entries.push([key,anyValue(v)]);
    }
    return [...buckets,other].filter(group=>group.entries.length);
  }
  function hrtime(value) {
    if(!Array.isArray(value) || value.length!==2 || !value.every(Number.isInteger) || value[0]<0 || value[1]<0 || value[1]>=1e9) return null;
    const number=value[0]+value[1]/1e9; return Number.isFinite(number)?number:null;
  }
  function operation(name) {
    const match=/^trajectory\.(\d{3})\.(user_message|assistant_message|tool_call|tool_result|session_started|done)$/.exec(String(name ?? ''));
    if(match && Number(match[1])>0) {
      const types={user_message:['user','用户输入'],assistant_message:['assistant','Agent 回复'],
        tool_call:['tool','工具调用'],tool_result:['tool','工具调用 · 结果'],
        session_started:['lifecycle','会话开始'],done:['lifecycle','执行结束']};
      const [kind,label]=types[match[2]];
      return {kind,label,type:match[2],sequence:match[1]};
    }
    const known={'POST /v1/messages':['request','接口请求'],'agent.execution':['agent','Agent 执行'],
      'officeclaw.agent.invoke':['agent','Agent 入口'],'dns.lookup':['network','域名解析']};
    if(Object.hasOwn(known,String(name))) {const [kind,label]=known[name];return {kind,label};}
    return null;
  }
  return {anyValue,attributes,semantic,status,spanKind,sdk,groups,hrtime,operation};
})();
if (typeof module !== 'undefined' && module.exports) module.exports = Otel;
