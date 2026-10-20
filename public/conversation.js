'use strict';

const chatState = {
  config: null, rules: [], applied: null, groups: [], groupPage: 0, groupPages: 0,
  selected: null, messages: [], messagePage: 0, messagePages: 0, totalMessages: 0,
  groupRequest: 0, messageRequest: 0, groupController: null, messageController: null,
  loading: false, loadingMessages: false, search: '', dirty: false, kindRules: [],
};
const mappingFields = [
  ['host_path', '主机字段 *', '用于侧边栏分组，例如 resourceAttributes["host.name"]'],
  ['time_path', '时间字段 *', '按该时间升序展示，例如 start；支持数组内时间'],
  ['session_path', '会话 ID（可选）', '同一主机下拆分不同对话'],
  ['message_path', '消息列表（可选）', '留空：一条记录是一条消息；也可选 messages 数组'],
  ['role_path', '消息角色（可选）', 'user / assistant / tool 等角色字段'],
  ['type_path', '事件类型（可选）', '可用事件类型的值识别工具记录'],
  ['content_path', '消息正文（可选）', '留空会完整显示原始消息 JSON'],
  ['attachment_path', '附件（可选）', '附件列表或附件对象'],
  ['tool_path', '工具调用（可选）', '工具名、参数、结果等完整结构'],
];

function chatText(value) {
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2) ?? '';
}

function chatTime(stamp, raw) {
  if (stamp === null || stamp === undefined) return raw ? `时间未识别：${raw}` : '未提供时间';
  const date = new Date(stamp * 1000);
  return Number.isNaN(date.getTime()) ? String(raw || stamp) : date.toLocaleString('zh-CN', { hour12: false });
}

function guessPath(candidates, suffixes = []) {
  for (const candidate of candidates) if (state.nodes.has(pathKey(candidate))) return [...candidate];
  return [...state.nodes.values()].find((node) => suffixes.some((suffix) =>
    suffix.length <= node.path.length && suffix.every((token, index) => token === node.path[node.path.length - suffix.length + index])))?.path || null;
}

function automaticChatConfig() {
  return {
    host_path: guessPath([['resourceAttributes', 'host.name'], ['resourceAttributes', 'host', 'name'], ['host.name'], ['host', 'name']], [['host.name'], ['host', 'name']]),
    time_path: guessPath([['start'], ['timestamp'], ['time']], [['start'], ['timestamp']]),
    session_path: guessPath([['conversation_id'], ['session_id'], ['conversation', 'id'], ['session', 'id']], [['conversation.id'], ['session.id'], ['conversation_id'], ['session_id']]),
    message_path: null,
    role_path: guessPath([['role'], ['message', 'role'], ['body', 'role']], [['role']]),
    type_path: guessPath([['type'], ['event', 'type']], []),
    content_path: guessPath([['content'], ['message', 'content'], ['body', 'content'], ['text'], ['body']], [['content'], ['text']]),
    attachment_path: guessPath([['attachments'], ['message', 'attachments']], [['attachments']]),
    tool_path: guessPath([['tool_calls'], ['tool_call'], ['message', 'tool_calls']], [['tool_calls'], ['tool_call']]),
    time_unit: 'auto', user_values: ['user', 'human'], agent_values: ['assistant', 'agent', 'ai'], tool_values: ['tool', 'function', 'tool_call', 'tool_result'],
  };
}

function chatNotice(text, error = false) {
  $('chat-notice').textContent = text;
  $('chat-notice').className = `chat-notice${error ? ' error' : ''}`;
}

function chatControls() {
  $('chat-load').disabled = !state.scan || chatState.loading;
  $('chat-more-groups').disabled = chatState.loading;
  $('chat-more-messages').disabled = chatState.loadingMessages || chatState.loading;
  $('chat-source').textContent = state.scan ? `${state.isDemo ? '示例数据 · ' : ''}${state.scan.root}` : '先在数据浏览页扫描目录';
  $('chat-source').title = state.scan?.root || '';
  $('chat-add-filter').disabled = chatState.rules.length >= 20;
  $('chat-add-kind').disabled = chatState.kindRules.length >= 20;
  $('chat-demo').disabled = !state.serviceReady || state.busy;
}

function markChatDirty() { chatState.dirty = true; }

function renderChatSettings() {
  const config = chatState.config;
  const mappings = $('chat-mappings');
  mappings.replaceChildren();
  mappingFields.forEach(([key, title, help]) => {
    const field = element('div', 'chat-mapping');
    field.append(element('label', '', title));
    field.append(createFieldPicker(config[key], title, (path) => { config[key] = path; markChatDirty(); },
      { optional: !['host_path', 'time_path'].includes(key), placeholder: '未映射', optionPrefix: `映射 ${title}` }));
    field.append(element('small', '', help)); mappings.append(field);
  });
  $('chat-time-unit').value = config.time_unit;
  for (const name of ['user', 'agent', 'tool']) $(`chat-${name}-values`).value = config[`${name}_values`].join(', ');
  renderFilterRules({ containerId: 'chat-field-filters', rules: chatState.rules, labelPrefix: '对话筛选条件', onChange: () => { markChatDirty(); chatControls(); } });
  renderKindRules();
}

function renderKindRules() {
  const container = $('chat-kind-rules'); container.replaceChildren();
  chatState.kindRules.forEach((rule, index) => {
    const card = element('section', 'chat-kind-rule');
    const heading = element('div', 'chat-kind-rule-heading');
    heading.append(element('span', '', `规则 ${index + 1}`));
    const kind = element('select'); kind.setAttribute('aria-label', `类型规则 ${index + 1} 的消息类型`);
    [['user', '用户'], ['assistant', 'Agent'], ['tool', '工具']].forEach(([value, label]) => {
      const option = element('option', '', label); option.value = value; kind.append(option);
    });
    kind.value = rule.kind; kind.addEventListener('change', () => { rule.kind = kind.value; markChatDirty(); });
    const remove = element('button', 'text-button', '删除规则'); remove.type = 'button';
    remove.addEventListener('click', () => { chatState.kindRules.splice(index, 1); renderKindRules(); markChatDirty(); chatControls(); });
    heading.append(kind, remove); card.append(heading);
    const fields = element('div'); fields.id = `chat-kind-fields-${index}`;
    card.append(fields);
    const add = element('button', 'text-button', '＋ 追加判断条件'); add.type = 'button';
    add.disabled = rule.conditions.length >= 20;
    add.addEventListener('click', () => { rule.conditions.push(newFilterRule()); renderKindRules(); markChatDirty(); });
    const override = element('div', 'chat-kind-override'); override.append(element('label', '', '该类型正文（可选）'));
    override.append(createFieldPicker(rule.content_path, `类型规则 ${index + 1} 的正文`, (path) => { rule.content_path = path; markChatDirty(); },
      { optional: true, placeholder: '沿用全局正文 / 完整消息', optionPrefix: `类型 ${index + 1} 正文` }));
    card.append(add, override); container.append(card);
    renderFilterRules({ containerId: fields.id, rules: rule.conditions, labelPrefix: `类型规则 ${index + 1} 条件`, onChange: markChatDirty });
  });
  if (!chatState.kindRules.length) container.append(element('p', 'chat-config-help', '当前使用角色字段与取值识别类型。未识别的记录会完整保留。'));
}

function renderChatTimes() {
  const wrapper = $('chat-time-fields'); wrapper.replaceChildren();
  ['from', 'to'].forEach((end, endpoint) => {
    const row = element('div', 'time-endpoint'); row.append(element('span', 'endpoint-label', endpoint ? '至' : '从'));
    ['day', 'hour', 'minute'].forEach((part, index) => {
      const label = element('label'); const input = element('input'); input.type = 'number';
      input.id = `chat-${end}-${part}`; input.min = index === 0 ? '1' : '0'; input.max = ['31', '23', '59'][index];
      input.setAttribute('aria-label', `对话${endpoint ? '结束' : '起始'}${['日', '小时', '分钟'][index]}`);
      input.value = (endpoint ? state.scan.max_time : state.scan.min_time)?.[index] ?? '';
      input.addEventListener('input', markChatDirty);
      label.append(input, element('span', '', ['日', '时', '分'][index])); row.append(label);
    }); wrapper.append(row);
  });
}

function onChatScan() {
  chatState.groupController?.abort(); chatState.messageController?.abort();
  chatState.groupRequest += 1; chatState.messageRequest += 1;
  chatState.loading = false; chatState.loadingMessages = false;
  chatState.applied = null; chatState.selected = null; chatState.groups = []; chatState.messages = [];
  const defaults = automaticChatConfig();
  let saved;
  try { saved = JSON.parse(localStorage.getItem('officeace-chat-config-v1')); } catch {}
  chatState.config = { ...defaults };
  chatState.kindRules = [];
  if (saved?.config && !state.isDemo) {
    mappingFields.forEach(([key]) => {
      const path = saved.config[key];
      if (Array.isArray(path) && state.nodes.has(pathKey(path))) chatState.config[key] = [...path];
      else if (path === null && !['host_path', 'time_path'].includes(key)) chatState.config[key] = null;
    });
    for (const name of ['user_values', 'agent_values', 'tool_values']) {
      if (Array.isArray(saved.config[name]) && saved.config[name].every((item) => typeof item === 'string')) chatState.config[name] = saved.config[name];
    }
    if (['auto', 's', 'ms', 'us', 'ns'].includes(saved.config.time_unit)) chatState.config.time_unit = saved.config.time_unit;
    if (Array.isArray(saved.config.kind_rules)) chatState.kindRules = saved.config.kind_rules.filter((rule) =>
      rule && ['user', 'assistant', 'tool'].includes(rule.kind) && Array.isArray(rule.conditions)).slice(0, 20).map((rule) => ({
        kind: rule.kind, content_path: Array.isArray(rule.content_path) && state.nodes.has(pathKey(rule.content_path)) ? rule.content_path : null,
        conditions: rule.conditions.filter((condition) => condition && Array.isArray(condition.path) && state.nodes.has(pathKey(condition.path))).slice(0, 20).map((condition) => ({
          ...newFilterRule(condition.path), ...condition,
          value: typeof condition.value === 'string' ? condition.value : '', from: typeof condition.from === 'string' ? condition.from : '', to: typeof condition.to === 'string' ? condition.to : '',
        })),
      }));
  }
  chatState.rules = saved?.rules && !state.isDemo ? saved.rules.filter((rule) => rule && Array.isArray(rule.path) && state.nodes.has(pathKey(rule.path)) &&
    ['contains', 'equals', 'between', 'exists', 'not_exists'].includes(rule.operator)).slice(0, 20).map((rule) => ({ ...newFilterRule(rule.path), ...rule,
      value: typeof rule.value === 'string' ? rule.value : '', from: typeof rule.from === 'string' ? rule.from : '', to: typeof rule.to === 'string' ? rule.to : '' })) : [];
  renderChatSettings(); renderChatTimes(); renderChatGroups();
  $('chat-title').textContent = '选择一个对话'; $('chat-subtitle').textContent = '按时间顺序回放完整消息。';
  $('chat-messages').replaceChildren(element('p', 'chat-placeholder', '点击“载入对话”，或先打开字段与筛选配置。'));
  $('chat-more-messages').hidden = true; $('chat-more-groups').hidden = true;
  $('chat-message-count').textContent = '完整保留内容 · 工具详情默认收起';
  chatNotice('数据已连接。确认字段映射后载入对话；对话页的筛选条件独立于数据浏览页。'); chatControls();
  if (!$('chat-page').hidden) loadChat();
}

function currentChatPayload() {
  if (!state.scan) throw new Error('请先在数据浏览页扫描数据目录。');
  const config = structuredClone(chatState.config);
  if (!config.host_path || !config.time_path) throw new Error('请先选择主机字段和时间字段。');
  for (const name of ['user', 'agent', 'tool']) config[`${name}_values`] = $(`chat-${name}-values`).value.split(/[,，\n]/).map((value) => value.trim()).filter(Boolean);
  config.time_unit = $('chat-time-unit').value;
  config.kind_rules = chatState.kindRules.flatMap((rule) => {
    const conditions = readFieldFilters(rule.conditions);
    return conditions.length ? [{ kind: rule.kind, conditions, content_path: rule.content_path }] : [];
  });
  const from = readTime('chat-from'), to = readTime('chat-to');
  if (from && to && from[0] * 1440 + from[1] * 60 + from[2] > to[0] * 1440 + to[1] * 60 + to[2]) throw new Error('目录时间起点不能晚于终点。');
  return { scan_id: state.scan.scan_id, config, field_filters: readFieldFilters(chatState.rules), time_from: from, time_to: to, deduplicate: $('chat-deduplicate').checked };
}

async function loadChat() {
  try {
    const payload = currentChatPayload();
    chatState.applied = payload; chatState.dirty = false; chatState.search = $('chat-search').value.trim();
    if (!state.isDemo) { try { localStorage.setItem('officeace-chat-config-v1', JSON.stringify({ config: payload.config, rules: chatState.rules })); } catch {} }
    $('chat-config-error').hidden = true;
    const loaded = await loadChatGroups(1);
    if (loaded) $('chat-config-dialog').close();
  } catch (error) {
    $('chat-config-error').textContent = error.message; $('chat-config-error').hidden = false;
    chatNotice(error.message, true);
  }
}

async function loadChatGroups(page = 1) {
  if (!chatState.applied) return false;
  const token = ++chatState.groupRequest;
  chatState.groupController?.abort(); chatState.groupController = new AbortController();
  if (page === 1) { chatState.messageRequest += 1; chatState.messageController?.abort(); chatState.loadingMessages = false; }
  chatState.loading = true; chatControls();
  if (page === 1) {
    chatState.groups = []; chatState.selected = null; chatState.messages = [];
    $('chat-title').textContent = '正在载入对话'; $('chat-subtitle').textContent = '';
    $('chat-groups').replaceChildren(element('p', 'chat-placeholder', '正在按主机分组…'));
    $('chat-messages').replaceChildren(element('p', 'chat-placeholder', '首次载入会建立本地对话索引，请稍候…'));
    $('chat-more-messages').hidden = true;
  }
  chatNotice('正在载入对话，首次映射会建立本地索引…');
  try {
    const result = await api('/api/conversations', { ...chatState.applied, page, page_size: 50, search: chatState.search }, chatState.groupController.signal);
    if (token !== chatState.groupRequest) return false;
    chatState.groups.push(...result.items); chatState.groupPage = result.page; chatState.groupPages = result.pages;
    $('chat-group-count').textContent = `${numberFormat.format(result.total)} 个`;
    $('chat-more-groups').hidden = result.page >= result.pages;
    renderChatGroups();
    chatNotice(result.total ? '按主机分组 · 按配置的消息时间升序回放 · 内容完整保留' : '没有匹配对话。请检查字段映射、消息数组路径、时间范围和记录筛选条件。');
    if (page === 1 && result.items.length) await selectChatGroup(result.items[0]);
    else if (page === 1) {
      $('chat-title').textContent = '没有匹配的对话';
      $('chat-messages').replaceChildren(element('p', 'chat-placeholder', '请调整字段映射或记录筛选条件后重新载入。'));
      $('chat-message-count').textContent = '0 条消息';
    }
    return true;
  } catch (error) {
    if (error.name !== 'AbortError' && token === chatState.groupRequest) {
      chatNotice(error.message, true);
      $('chat-config-error').textContent = error.message; $('chat-config-error').hidden = false;
      $('chat-groups').replaceChildren(element('p', 'chat-placeholder', '载入失败，请检查配置并重新载入。'));
      $('chat-messages').replaceChildren(element('p', 'chat-placeholder', error.message));
    }
    return false;
  } finally { if (token === chatState.groupRequest) { chatState.loading = false; chatControls(); } }
}

function renderChatGroups() {
  const container = $('chat-groups'); container.replaceChildren();
  const hosts = new Map();
  chatState.groups.forEach((group) => {
    const key = group.host === null ? null : group.host;
    if (!hosts.has(key)) hosts.set(key, []); hosts.get(key).push(group);
  });
  hosts.forEach((groups, host) => {
    const section = element('section', 'chat-host-group');
    const title = element('h3', '', host === null ? '未提供主机' : host || '空主机名'); title.title = host || '该字段不存在或为空'; section.append(title);
    groups.forEach((group) => {
      const button = element('button', `chat-session${chatState.selected?.id === group.id ? ' active' : ''}`);
      button.type = 'button'; button.setAttribute('aria-label', `对话 ${group.host ?? '未提供主机'} / ${group.session ?? '默认对话'}`);
      button.setAttribute('aria-pressed', String(chatState.selected?.id === group.id));
      const name = group.session === null ? '完整对话' : group.session || '空会话 ID';
      button.append(element('strong', '', name), element('span', '', `${numberFormat.format(group.count)} 条消息 · ${chatTime(group.last_stamp, null)}`));
      if (group.missing_time) button.append(element('small', '', `${group.missing_time} 条时间缺失 / 无效`));
      button.addEventListener('click', () => selectChatGroup(group)); section.append(button);
    }); container.append(section);
  });
  if (!hosts.size) container.append(element('p', 'chat-placeholder', '尚无对话。'));
}

async function selectChatGroup(group) {
  chatState.selected = group; chatState.messages = []; chatState.messagePage = 0;
  $('chat-title').textContent = group.host === null ? '未提供主机' : group.host || '空主机名';
  $('chat-subtitle').textContent = `${group.session ?? '此主机的完整对话'} · ${numberFormat.format(group.count)} 条消息${group.missing_time ? ` · ${group.missing_time} 条时间缺失或无效` : ''}`;
  $('chat-messages').replaceChildren(element('p', 'chat-placeholder', '正在载入消息…'));
  renderChatGroups(); await loadChatMessages(1);
}

async function loadChatMessages(page) {
  const group = chatState.selected; if (!group || !chatState.applied) return;
  const token = ++chatState.messageRequest;
  chatState.messageController?.abort(); chatState.messageController = new AbortController();
  chatState.loadingMessages = true; chatControls();
  try {
    const result = await api('/api/conversation/messages', { ...chatState.applied, host: group.host, session: group.session, page, page_size: 100 }, chatState.messageController.signal);
    if (token !== chatState.messageRequest) return;
    const container = $('chat-messages');
    if (page === 1) { container.replaceChildren(); chatState.messages = []; container.scrollTop = 0; }
    result.items.forEach((item) => container.append(renderChatMessage(item)));
    chatState.messages.push(...result.items); chatState.messagePage = result.page; chatState.messagePages = result.pages; chatState.totalMessages = result.total;
    $('chat-message-count').textContent = `已显示 ${numberFormat.format(chatState.messages.length)} / ${numberFormat.format(result.total)} 条 · 时间升序`;
    $('chat-more-messages').hidden = result.page >= result.pages;
  } catch (error) {
    if (error.name !== 'AbortError' && token === chatState.messageRequest) chatNotice(error.message, true);
  } finally { if (token === chatState.messageRequest) { chatState.loadingMessages = false; chatControls(); } }
}

function renderToolDetails(item) {
  const details = element('details', 'chat-tool');
  const summary = element('summary');
  const tool = item.tool;
  const name = !Array.isArray(tool) && tool && typeof tool === 'object' ? tool.name || tool.function?.name : null;
  summary.append(icon('code'), element('span', '', name ? `工具调用 · ${name}` : item.kind === 'tool' ? '工具调用 / 返回结果' : '工具调用'), element('small', '', '点击展开完整详情'));
  details.append(summary);
  const content = element('div', 'chat-tool-content');
  if (item.kind === 'tool') content.append(element('h4', '', '完整工具返回内容'), element('pre', '', chatText(item.content)));
  if (tool !== null) content.append(element('h4', '', '工具字段'), element('pre', '', chatText(tool)));
  content.append(element('h4', '', '完整原始消息'), element('pre', '', chatText(item.raw)));
  details.append(content); return details;
}

function renderChatMessage(item) {
  const article = element('article', `chat-message ${item.kind === 'user' ? 'is-user' : 'is-agent'} kind-${item.kind}`);
  const labels = { user: '用户', assistant: 'Agent', tool: '工具', record: item.role || '未识别角色的记录' };
  const avatar = element('div', 'message-avatar', item.kind === 'user' ? 'U' : item.kind === 'tool' ? '⌘' : 'A');
  const column = element('div', 'message-column'); column.append(element('div', 'message-label', labels[item.kind]));
  const bubble = element('div', 'message-bubble');
  const hasTool = item.tool !== null && item.tool !== '' && !(Array.isArray(item.tool) && !item.tool.length) &&
    !(typeof item.tool === 'object' && !Array.isArray(item.tool) && !Object.keys(item.tool).length);
  if (item.kind !== 'tool') bubble.append(element('pre', 'message-body', chatText(item.content)));
  if (item.kind === 'tool' || (hasTool && item.kind !== 'user')) bubble.append(renderToolDetails(item));
  if (item.attachments !== null) {
    const attachments = element('div', 'message-attachments');
    const files = Array.isArray(item.attachments) ? item.attachments : [item.attachments];
    files.forEach((file, index) => {
      const attachment = element('details', 'chat-attachment');
      const summary = element('summary');
      const name = file && typeof file === 'object' ? file.name || file.filename || file.file_name || `附件 ${index + 1}` : `附件 ${index + 1}`;
      summary.append(icon('file'), element('span', '', String(name)), element('small', '', '附件详情'));
      attachment.append(summary, element('pre', '', chatText(file))); attachments.append(attachment);
    }); bubble.append(attachments);
  }
  const time = element('div', 'message-time', chatTime(item.timestamp, item.time_raw)); time.title = item.time_raw || '未提供原始时间';
  bubble.append(time); column.append(bubble);
  const provenance = element('details', 'chat-provenance');
  provenance.append(element('summary', '', `${item.source}${item.copies > 1 ? ` · 已合并 ${item.copies} 份相同记录` : ''} · 查看来源与原始记录`));
  const provenanceBody = element('div'); let loaded = false;
  provenance.addEventListener('toggle', async () => {
    if (!provenance.open || loaded) return;
    loaded = true; provenanceBody.textContent = '正在读取原始记录…';
    try {
      const data = await api('/api/record', { scan_id: chatState.applied.scan_id, id: item.record_id });
      provenanceBody.replaceChildren(element('p', '', `全目录共 ${data.total} 份相同记录`),
        element('pre', '', data.copies.map((copy) => `${copy.source} · 第 ${copy.index} 条`).join('\n') + (data.has_more ? '\n仅显示前 200 个来源' : '')),
        element('pre', '', JSON.stringify(data.record, null, 2)));
    } catch (error) { loaded = false; provenanceBody.textContent = error.message; }
  });
  provenance.append(provenanceBody); column.append(provenance); article.append(avatar, column);
  if (item.kind === 'user' && hasTool) {
    const fragment = document.createDocumentFragment();
    fragment.append(article, renderChatMessage({ ...item, kind: 'tool', role: 'tool', content: item.tool, attachments: null }));
    return fragment;
  }
  return article;
}

function openChatSettings() {
  if (!state.scan) { switchPage('data'); message('请先扫描数据目录，再配置对话字段。', 'error'); return; }
  if (!chatState.config) onChatScan();
  $('chat-config-dialog').showModal();
}

window.addEventListener('officeace:scan', onChatScan);
window.addEventListener('officeace:ready', chatControls);
window.addEventListener('officeace:chat-open', () => {
  chatControls();
  if (state.scan && !chatState.config) onChatScan();
});
$('chat-settings-button').addEventListener('click', openChatSettings);
$('chat-welcome-settings').addEventListener('click', openChatSettings);
$('chat-config-close').addEventListener('click', () => $('chat-config-dialog').close());
$('chat-load').addEventListener('click', loadChat);
$('chat-config-form').addEventListener('submit', (event) => { event.preventDefault(); loadChat(); });
$('chat-time-unit').addEventListener('change', () => { if (chatState.config) chatState.config.time_unit = $('chat-time-unit').value; markChatDirty(); });
$('chat-deduplicate').addEventListener('change', markChatDirty);
for (const name of ['user', 'agent', 'tool']) $(`chat-${name}-values`).addEventListener('input', () => {
  if (chatState.config) chatState.config[`${name}_values`] = $(`chat-${name}-values`).value.split(/[,，\n]/).map((value) => value.trim()).filter(Boolean);
  markChatDirty();
});
$('chat-reset-config').addEventListener('click', () => { chatState.config = automaticChatConfig(); chatState.rules = []; chatState.kindRules = []; renderChatSettings(); renderChatTimes(); markChatDirty(); chatControls(); });
$('chat-add-filter').addEventListener('click', () => {
  if (chatState.rules.length >= 20) return;
  chatState.rules.push(newFilterRule()); renderChatSettings(); markChatDirty(); chatControls();
});
$('chat-add-kind').addEventListener('click', () => {
  if (chatState.kindRules.length >= 20) return;
  chatState.kindRules.push({ kind: 'user', content_path: null, conditions: [newFilterRule()] });
  renderKindRules(); markChatDirty(); chatControls();
});
$('chat-more-groups').addEventListener('click', () => loadChatGroups(chatState.groupPage + 1));
$('chat-more-messages').addEventListener('click', () => loadChatMessages(chatState.messagePage + 1));
$('chat-search').addEventListener('input', () => {
  chatState.search = $('chat-search').value.trim();
  clearTimeout(chatState.searchTimer);
  chatState.searchTimer = setTimeout(() => { if (chatState.applied) loadChatGroups(1); }, 300);
});
$('chat-demo').addEventListener('click', async () => {
  if (!state.chatDemoRoot || state.busy) return;
  await scanDirectory(state.chatDemoRoot, true);
});
chatControls();
