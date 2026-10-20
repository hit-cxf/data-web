'use strict';

const $ = (id) => document.getElementById(id);
const MISSING = Symbol('missing');
const pathKey = (path) => JSON.stringify(path);
const numberFormat = new Intl.NumberFormat('zh-CN');
const state = {
  scan: null, result: null, nodes: new Map(), roots: [], terminals: [], selected: new Set(),
  collapsed: new Set(), view: 'table', busy: false, request: 0, controller: null,
  demoRoot: null, isDemo: false, query: null, pageSize: 50, sort: 'time_desc', expanded: new Set(),
  filterRules: [], nextFilterId: 1,
  serviceReady: false,
};

function element(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

function displayPath(path) {
  return path.map((part, index) => {
    if (part === '[]') return '[]';
    if (/^[a-zA-Z_$][\w$]*$/.test(part)) return `${index ? '.' : ''}${part}`;
    return `[${JSON.stringify(part)}]`;
  }).join('');
}

function message(text, kind = '') {
  $('message').textContent = text;
  $('message').className = `message ${kind}`;
  $('message').hidden = !text;
}

function showWarnings(...groups) {
  const warnings = [...new Set(groups.flat().filter(Boolean))];
  $('warnings').hidden = warnings.length === 0;
  $('warnings-summary').textContent = `${warnings.length} 条数据读取提示`;
  $('warnings-list').replaceChildren(...warnings.map((warning) => element('li', '', warning)));
}

async function api(path, body, signal) {
  const response = await fetch(path, body === undefined ? { signal } : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  });
  let data;
  try { data = await response.json(); } catch { throw new Error(`服务返回了无效响应（${response.status}）`); }
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}

function updateControls() {
  $('scan-button').disabled = state.busy || !state.serviceReady;
  $('demo-button').disabled = state.busy || !state.serviceReady || !state.demoRoot;
  $('query-button').disabled = state.busy || !state.serviceReady || !state.scan;
  $('add-filter').disabled = state.busy || !state.scan || state.filterRules.length >= 20;
  $('reset-filters').disabled = state.busy || !state.scan;
  $('sort-order').disabled = state.busy || !state.scan;
  $('page-size').disabled = state.busy || !state.scan;
  $('export-button').disabled = state.busy || !state.result?.items?.length || !state.selected.size;
  $('previous-page').disabled = state.busy || !state.result || state.result.page <= 1;
  $('next-page').disabled = state.busy || !state.result || state.result.page >= state.result.pages;
  $('results-body').setAttribute('aria-busy', String(state.busy));
}

function startRequest(text) {
  state.controller?.abort();
  state.controller = new AbortController();
  state.request += 1;
  state.busy = true;
  updateControls();
  message(text, 'loading');
  return { token: state.request, signal: state.controller.signal };
}

function setTime(prefix, time) {
  ['day', 'hour', 'minute'].forEach((part, index) => { $(`${prefix}-${part}`).value = time?.[index] ?? ''; });
}

function formatTime(time) {
  return time ? `${String(time[0]).padStart(2, '0')} 日 ${String(time[1]).padStart(2, '0')}:${String(time[2]).padStart(2, '0')}` : '—';
}

function resetFilters() {
  setTime('from', state.scan?.min_time);
  setTime('to', state.scan?.max_time);
  const start = state.nodes.get(pathKey(['start']));
  state.filterRules = [newFilterRule(start?.path || null)];
  renderFilterRules();
}

function newFilterRule(path = null) {
  return { id: state.nextFilterId++, path, operator: 'contains', value: '', from: '', to: '' };
}

function readFieldFilters() {
  return state.filterRules.flatMap((rule, index) => {
    const active = rule.operator === 'between' ? rule.from.trim() || rule.to.trim() : rule.value.trim();
    if (!active) return [];
    if (!rule.path || !state.nodes.has(pathKey(rule.path))) {
      throw new Error(`请先为第 ${index + 1} 个条件选择完整的筛选字段。`);
    }
    return [rule.operator === 'between'
      ? { path: [...rule.path], operator: rule.operator, from: rule.from.trim(), to: rule.to.trim() }
      : { path: [...rule.path], operator: rule.operator, value: rule.value.trim() }];
  });
}

function renderFilterRules() {
  const container = $('field-filters');
  container.replaceChildren();
  if (!state.filterRules.length) container.append(element('p', 'no-filter-note', '未添加字段条件，查询时间区间内的全部记录。'));
  state.filterRules.forEach((rule, index) => {
    const row = element('div', 'filter-rule');
    const picker = element('details', 'filter-field-picker');
    const summary = element('summary');
    summary.setAttribute('aria-label', `筛选条件 ${index + 1} 的字段`);
    const selected = element('span', 'filter-selected-path', rule.path ? displayPath(rule.path) : '选择筛选字段');
    selected.title = selected.textContent;
    summary.append(selected, icon('chevron'));
    picker.append(summary);
    const dropdown = element('div', 'filter-field-dropdown');
    const search = element('input', 'filter-field-search');
    search.type = 'search';
    search.placeholder = '搜索字段，例如 host.name';
    search.setAttribute('aria-label', `搜索第 ${index + 1} 个筛选字段`);
    const options = element('div', 'filter-field-options');
    const drawOptions = () => {
      const keyword = search.value.trim().toLowerCase();
      const matches = [...state.nodes.values()].filter((node) => displayPath(node.path).toLowerCase().includes(keyword));
      options.replaceChildren();
      matches.slice(0, 200).forEach((node) => {
        const option = element('button', 'filter-field-option');
        option.type = 'button';
        option.title = displayPath(node.path);
        option.setAttribute('aria-label', `筛选字段 ${displayPath(node.path)}`);
        option.append(element('span', '', displayPath(node.path)), element('small', '', typeLabel(node.types)));
        option.addEventListener('click', () => {
          rule.path = [...node.path];
          selected.textContent = displayPath(rule.path);
          selected.title = selected.textContent;
          picker.open = false;
          summary.focus();
        });
        options.append(option);
      });
      if (!matches.length) options.append(element('p', 'no-filter-note', '没有匹配的字段。请尝试其他字段名。'));
      if (matches.length > 200) options.append(element('p', 'no-filter-note', `显示前 200 项，共 ${matches.length} 项；输入更多关键字缩小范围。`));
    };
    search.addEventListener('input', drawOptions);
    search.addEventListener('keydown', (event) => {
      // Searching for a field must not accidentally submit the query form.
      if (event.key === 'Enter') { event.preventDefault(); options.querySelector('button')?.focus(); }
      if (event.key === 'Escape') { event.preventDefault(); picker.open = false; summary.focus(); }
    });
    picker.addEventListener('toggle', () => {
      if (picker.open) {
        container.querySelectorAll('details[open]').forEach((other) => { if (other !== picker) other.open = false; });
        drawOptions();
        search.focus();
      }
    });
    dropdown.append(search, options);
    picker.append(dropdown);
    const operator = element('select', 'filter-operator');
    operator.setAttribute('aria-label', `筛选条件 ${index + 1} 的匹配方式`);
    [['contains', '包含'], ['equals', '等于'], ['between', '区间']].forEach(([value, text]) => {
      const option = element('option', '', text);
      option.value = value;
      operator.append(option);
    });
    operator.value = rule.operator;
    operator.addEventListener('change', () => { rule.operator = operator.value; renderFilterRules(); });
    const values = element('div', 'filter-rule-values');
    const addValueInput = (key, placeholder, label) => {
      const input = element('input');
      input.type = 'text';
      input.autocomplete = 'off';
      input.value = rule[key];
      input.placeholder = placeholder;
      input.setAttribute('aria-label', `筛选条件 ${index + 1} 的${label}`);
      input.addEventListener('input', () => { rule[key] = input.value; });
      values.append(input);
    };
    if (rule.operator === 'between') {
      addValueInput('from', '起始值（数字或 ISO 日期）', '起始值');
      values.append(element('span', '', '—'));
      addValueInput('to', '结束值（可留空）', '结束值');
    } else addValueInput('value', '输入字段值的关键字', '关键字');
    const remove = element('button', 'icon-button remove-filter', '×');
    remove.type = 'button';
    remove.setAttribute('aria-label', `删除筛选条件 ${index + 1}`);
    remove.addEventListener('click', () => {
      state.filterRules = state.filterRules.filter((item) => item.id !== rule.id);
      renderFilterRules();
      updateControls();
    });
    row.append(picker, operator, values, remove);
    container.append(row);
  });
}

function readTime(prefix) {
  const values = ['day', 'hour', 'minute'].map((part) => $(`${prefix}-${part}`).value.trim());
  if (values.every((value) => value === '')) return null;
  if (values.some((value) => value === '')) throw new Error('时间端点请完整填写日、时、分，或全部留空。');
  const nums = values.map(Number);
  if (nums.some((value) => !Number.isInteger(value)) || nums[0] < 1 || nums[0] > 31 || nums[1] < 0 || nums[1] > 23 || nums[2] < 0 || nums[2] > 59) {
    throw new Error('时间范围无效：日为 1–31，小时为 0–23，分钟为 0–59。');
  }
  return nums;
}

function readQuery() {
  const from = readTime('from');
  const to = readTime('to');
  const timeValue = (value) => value[0] * 1440 + value[1] * 60 + value[2];
  if (from && to && timeValue(from) > timeValue(to)) throw new Error('起始文件时间不能晚于结束文件时间。');
  return {
    time_from: from, time_to: to,
    field_filters: readFieldFilters(),
  };
}

async function scanDirectory(root, isDemo = false) {
  if (!state.serviceReady) { message('请先重启更新后的 server.py，再刷新页面。', 'error'); return; }
  root = root.trim();
  if (!root) { message('请输入数据根目录的绝对路径。', 'error'); $('root-path').focus(); return; }
  const { token, signal } = startRequest('正在扫描目录并解析 JSON，请稍候…');
  try {
    const scan = await api('/api/scan', { root }, signal);
    if (token !== state.request) return;
    state.scan = scan;
    state.result = null;
    state.isDemo = isDemo;
    state.expanded.clear();
    $('root-path').value = scan.root;
    if (!isDemo) { try { localStorage.setItem('officeace-directory', scan.root); } catch {} }
    $('source-state').textContent = isDemo ? '示例数据' : '已连接';
    $('source-state').className = `tag ${isDemo ? 'demo' : 'success'}`;
    $('stat-records').textContent = numberFormat.format(scan.record_count);
    $('stat-files').textContent = numberFormat.format(scan.file_count);
    $('stat-fields').textContent = numberFormat.format(scan.fields.length);
    $('scan-detail').textContent = `${scan.days.length} 天数据 · ${isDemo ? '内置示例目录' : '本地目录已连接'}`;
    $('available-time').textContent = scan.min_time ? `可用：${formatTime(scan.min_time)} — ${formatTime(scan.max_time)}` : '目录内暂无有效 JSON 记录';
    buildSchema(scan.fields);
    resetFilters();
    selectDefaults();
    state.query = readQuery();
    showWarnings(scan.warnings || []);
    await queryPage(1, { token, signal });
  } catch (error) {
    if (error.name === 'AbortError' || token !== state.request) return;
    if (state.scan) {
      $('root-path').value = state.scan.root;
      message(`${error.message} 已保留之前连接的目录和查询结果。`, 'error');
    } else message(error.message, 'error');
  } finally {
    if (token === state.request) { state.busy = false; updateControls(); }
  }
}

async function queryPage(page = 1, activeRequest = null) {
  if (!state.scan) return;
  const { token, signal } = activeRequest || startRequest('正在筛选记录，首次使用的字段会建立本地索引…');
  try {
    const result = await api('/api/query', {
      scan_id: state.scan.scan_id, ...state.query, page,
      page_size: state.pageSize, sort: state.sort,
    }, signal);
    if (token !== state.request) return;
    state.result = result;
    state.expanded.clear();
    renderResults();
    showWarnings(state.scan.warnings || [], result.warnings || []);
    message('');
  } catch (error) {
    if (error.name === 'AbortError' || token !== state.request) return;
    // A failed query cannot leave stale rows looking like the requested result.
    state.result = null;
    renderResults();
    setEmpty('查询未完成', '请检查筛选条件或重新扫描目录后再试。', 'search');
    message(error.message, 'error');
  } finally {
    if (token === state.request) { state.busy = false; updateControls(); }
  }
}

function buildSchema(fields) {
  state.nodes.clear();
  state.roots = [];
  state.terminals = [];
  state.collapsed.clear();
  for (const field of fields) {
    if (!Array.isArray(field.path) || !field.path.length) continue;
    for (let depth = 1; depth <= field.path.length; depth += 1) {
      const path = field.path.slice(0, depth);
      const key = pathKey(path);
      if (!state.nodes.has(key)) {
        const node = { key, path, name: path.at(-1), types: [], children: [], terminals: [], depth: depth - 1 };
        state.nodes.set(key, node);
        if (depth === 1) state.roots.push(node);
        else state.nodes.get(pathKey(path.slice(0, -1))).children.push(node);
      }
      if (depth === field.path.length) state.nodes.get(key).types = field.types || [];
    }
  }
  function prepare(node) {
    const hasScalar = node.types.some((type) => !['array', 'object'].includes(type));
    node.selectable = node.children.length === 0 || hasScalar;
    if (node.selectable) state.terminals.push(node);
    node.terminals = [...(node.selectable ? [node.key] : []), ...node.children.flatMap(prepare)];
    if (node.depth >= 2 && node.children.length) state.collapsed.add(node.key);
    return node.terminals;
  }
  state.roots.forEach(prepare);
  $('field-search').value = '';
}

function selectDefaults() {
  state.selected.clear();
  const candidates = [[['start']], [['host.name'], ['host', 'name']], [['user.id'], ['user', 'id']]];
  for (const paths of candidates) {
    const node = paths.map((path) => state.nodes.get(pathKey(path))).find(Boolean);
    if (node) node.terminals.forEach((key) => state.selected.add(key));
  }
  if (!state.selected.size) state.terminals.slice(0, 3).forEach((node) => state.selected.add(node.key));
  selectionChanged();
}

function typeLabel(types) {
  const labels = { string: 'str', number: 'num', integer: 'num', boolean: 'bool', object: '{}', array: '[]', null: 'null' };
  return [...new Set(types.map((type) => labels[type] || type))].join(' · ');
}

function renderTree() {
  const search = $('field-search').value.trim().toLowerCase();
  const tree = $('field-tree');
  tree.replaceChildren();
  if (!state.roots.length) {
    tree.append(element('div', 'field-empty', state.scan ? '当前目录没有可展示的字段' : '扫描目录后，在这里选择需要展示的字段'));
    return;
  }
  const matches = new Map();
  function isMatch(node) {
    if (matches.has(node.key)) return matches.get(node.key);
    const result = !search || displayPath(node.path).toLowerCase().includes(search) || node.children.some(isMatch);
    matches.set(node.key, result);
    return result;
  }
  function appendNode(node, parent) {
    if (!isMatch(node)) return;
    const wrapper = element('div', 'tree-node');
    const row = element('div', 'tree-row');
    row.style.setProperty('--depth', node.depth);
    const count = node.terminals.filter((key) => state.selected.has(key)).length;
    if (count) row.classList.add('chosen');
    const open = search || !state.collapsed.has(node.key);
    if (node.children.length) {
      const toggle = element('button', 'tree-toggle');
      toggle.type = 'button';
      toggle.setAttribute('aria-label', `${open ? '折叠' : '展开'} ${displayPath(node.path)}`);
      toggle.setAttribute('aria-expanded', String(Boolean(open)));
      toggle.append(icon('chevron'));
      toggle.addEventListener('click', () => {
        if (state.collapsed.has(node.key)) state.collapsed.delete(node.key); else state.collapsed.add(node.key);
        renderTree();
      });
      row.append(toggle);
    } else row.append(element('span', 'tree-spacer'));
    const checkbox = element('input');
    checkbox.type = 'checkbox';
    checkbox.checked = count === node.terminals.length;
    checkbox.indeterminate = count > 0 && count < node.terminals.length;
    checkbox.setAttribute('aria-label', `展示字段 ${displayPath(node.path)}`);
    checkbox.addEventListener('change', () => {
      node.terminals.forEach((key) => checkbox.checked ? state.selected.add(key) : state.selected.delete(key));
      selectionChanged();
    });
    const label = element('label', 'tree-label');
    label.title = `${displayPath(node.path)} · ${node.types.join(' / ')}`;
    label.append(checkbox, element('span', 'tree-name', node.name), element('span', 'tree-type', typeLabel(node.types)));
    row.append(label);
    wrapper.append(row);
    if (node.children.length && open) {
      const children = element('div', 'tree-children');
      node.children.forEach((child) => appendNode(child, children));
      wrapper.append(children);
    }
    parent.append(wrapper);
  }
  state.roots.forEach((node) => appendNode(node, tree));
  if (!tree.children.length) tree.append(element('div', 'field-empty', `没有找到“${$('field-search').value.trim()}”`));
}

function selectedNodes() {
  return state.terminals.filter((node) => state.selected.has(node.key));
}

function selectionChanged() {
  $('selected-count').textContent = numberFormat.format(state.selected.size);
  renderTree();
  const chips = $('selected-chips');
  chips.replaceChildren();
  const nodes = selectedNodes();
  nodes.slice(0, 30).forEach((node) => {
    const chip = element('span', 'field-chip');
    chip.title = displayPath(node.path);
    chip.append(element('span', '', displayPath(node.path)));
    const remove = element('button', '', '×');
    remove.type = 'button';
    remove.setAttribute('aria-label', `取消展示 ${displayPath(node.path)}`);
    remove.addEventListener('click', () => { state.selected.delete(node.key); selectionChanged(); });
    chip.append(remove);
    chips.append(chip);
  });
  if (nodes.length > 30) chips.append(element('span', 'chip-more', `另 ${nodes.length - 30} 个字段`));
  if (!nodes.length) chips.append(element('span', 'muted', '尚未选择展示字段'));
  renderResults();
  updateControls();
}

function makeProjection() {
  const root = { selected: false, children: new Map() };
  for (const node of selectedNodes()) {
    let current = root;
    for (const token of node.path) {
      if (!current.children.has(token)) current.children.set(token, { selected: false, children: new Map() });
      current = current.children.get(token);
    }
    current.selected = true;
  }
  return root;
}

function project(value, trie) {
  if (value === null || typeof value !== 'object') return trie.selected ? value : MISSING;
  if (Array.isArray(value)) {
    const itemTrie = trie.children.get('[]');
    if (!itemTrie) return trie.selected && value.length === 0 ? [] : MISSING;
    return value.map((item) => {
      const projected = project(item, itemTrie);
      // Preserve array positions when a selected field is absent in one element.
      return projected === MISSING ? (item !== null && typeof item === 'object' ? (Array.isArray(item) ? [] : {}) : null) : projected;
    });
  }
  const output = Object.create(null);
  let found = false;
  for (const [key, child] of trie.children) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
    const projected = project(value[key], child);
    if (projected !== MISSING) { output[key] = projected; found = true; }
  }
  return found || trie.selected || trie.children.size ? output : MISSING;
}

function projectedRecords() {
  const trie = makeProjection();
  return (state.result?.items || []).map((item) => {
    const record = project(item.record, trie);
    return record === MISSING ? {} : record;
  });
}

function readPath(value, path, index = 0) {
  if (index === path.length) return value;
  if (path[index] === '[]' && Array.isArray(value)) return value.map((item) => readPath(item, path, index + 1));
  if (value === null || typeof value !== 'object' || !Object.prototype.hasOwnProperty.call(value, path[index])) return MISSING;
  return readPath(value[path[index]], path, index + 1);
}

function valueText(value) {
  if (value === MISSING) return '—';
  if (value === null) return 'null';
  if (Array.isArray(value)) return `[${value.map((item) => typeof item === 'string' ? JSON.stringify(item) : valueText(item)).join(', ')}]`;
  if (typeof value === 'object') return Object.keys(value).length ? '{…}' : '{}';
  if (value === '') return '""';
  return String(value);
}

function setEmpty(title, description, symbol = 'folder') {
  const empty = $('empty-state');
  empty.hidden = false;
  empty.querySelector('h3').textContent = title;
  empty.querySelector('p').textContent = description;
  empty.querySelector('use').setAttribute('href', `#i-${symbol}`);
  $('table-panel').hidden = true;
  $('json-panel').hidden = true;
}

function renderResults() {
  const result = state.result;
  const nodes = selectedNodes();
  $('table-tab').classList.toggle('active', state.view === 'table');
  $('json-tab').classList.toggle('active', state.view === 'json');
  $('table-tab').setAttribute('aria-selected', String(state.view === 'table'));
  $('json-tab').setAttribute('aria-selected', String(state.view === 'json'));
  $('result-count').textContent = result ? `${numberFormat.format(result.total)} 条` : '等待加载';
  $('result-count').className = `tag ${result ? 'success' : 'neutral'}`;
  $('result-detail').textContent = result
    ? `时间区间内 ${numberFormat.format(result.file_count)} 个文件 · 查询耗时 ${numberFormat.format(result.elapsed_ms)} ms${state.isDemo ? ' · 当前为示例数据' : ''}`
    : '连接数据目录，开始探索回流记录';
  if (!result) setEmpty('从一个数据目录开始', '输入本机目录并扫描，或加载示例数据体验。你的数据只在本地服务中读取和处理。');
  else if (result.total === 0) setEmpty('没有找到匹配的记录', '试试放宽时间区间、调整筛选条件，或检查目录中的 JSON 数据。', 'search');
  else if (!nodes.length) setEmpty('选择你关心的字段', '勾选左侧任意字段，即可查看表格和对应的嵌套 JSON。', 'layers');
  else {
    $('empty-state').hidden = true;
    $('table-panel').hidden = state.view !== 'table';
    $('json-panel').hidden = state.view !== 'json';
    if (state.view === 'table') renderTable(nodes);
    else $('json-output').textContent = JSON.stringify(projectedRecords(), null, 2);
  }
  const total = result?.total || 0;
  const begin = total ? (result.page - 1) * result.page_size + 1 : 0;
  const end = result ? Math.min(result.page * result.page_size, total) : 0;
  $('page-summary').textContent = total ? `${numberFormat.format(begin)}–${numberFormat.format(end)} / 共 ${numberFormat.format(total)} 条` : '暂无记录';
  $('page-indicator').textContent = `${total ? result.page : 0} / ${result?.pages || 0}`;
  updateControls();
}

function renderTable(nodes) {
  const table = $('results-table');
  const header = element('tr');
  header.append(element('th', 'expand-column'), element('th', 'row-index', '#'));
  for (const node of nodes) {
    const th = element('th', '', displayPath(node.path));
    th.title = `${displayPath(node.path)} · ${node.types.join(' / ')}`;
    header.append(th);
  }
  table.tHead.replaceChildren(header);
  const fragment = document.createDocumentFragment();
  const trie = makeProjection();
  state.result.items.forEach((item, index) => {
    const row = element('tr');
    const expandCell = element('td', 'expand-column');
    const expand = element('button', 'expand-record');
    expand.type = 'button';
    expand.setAttribute('aria-label', `查看第 ${(state.result.page - 1) * state.result.page_size + index + 1} 条记录的已选字段 JSON`);
    expand.setAttribute('aria-expanded', String(state.expanded.has(item.id)));
    expand.append(icon('chevron'));
    expandCell.append(expand);
    row.append(expandCell, element('td', 'row-index', String((state.result.page - 1) * state.result.page_size + index + 1).padStart(2, '0')));
    for (const node of nodes) {
      const value = readPath(item.record, node.path);
      const cell = element('td', value === MISSING ? 'missing-value' : value === null ? 'null-value' : typeof value === 'number' ? 'number-value' : typeof value === 'boolean' ? 'boolean-value' : '', valueText(value));
      cell.title = value === MISSING ? '此记录中不存在该字段' : valueText(value);
      row.append(cell);
    }
    const detail = element('tr', 'detail-row');
    const detailCell = element('td');
    detailCell.colSpan = nodes.length + 2;
    const wrapper = element('div', 'detail-wrapper');
    const meta = element('div', 'detail-meta');
    meta.append(element('span', '', '来源文件'), element('code', '', item.source));
    wrapper.append(meta, element('pre', '', JSON.stringify(project(item.record, trie), null, 2)));
    detailCell.append(wrapper);
    detail.append(detailCell);
    detail.hidden = !state.expanded.has(item.id);
    expand.addEventListener('click', () => {
      if (state.expanded.has(item.id)) state.expanded.delete(item.id); else state.expanded.add(item.id);
      expand.setAttribute('aria-expanded', String(state.expanded.has(item.id)));
      detail.hidden = !state.expanded.has(item.id);
    });
    fragment.append(row, detail);
  });
  table.tBodies[0].replaceChildren(fragment);
}

function exportCurrentPage() {
  if (state.busy || !state.result?.items?.length || !state.selected.size) return;
  const blob = new Blob([JSON.stringify(projectedRecords(), null, 2)], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = element('a');
  anchor.href = url;
  anchor.download = `officeACE-${state.isDemo ? 'demo-' : ''}page-${state.result.page}.json`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$('source-form').addEventListener('submit', (event) => {
  event.preventDefault();
  if (!state.busy) scanDirectory($('root-path').value, $('root-path').value.trim() === state.demoRoot);
});
$('demo-button').addEventListener('click', () => scanDirectory(state.demoRoot, true));
$('filter-form').addEventListener('submit', (event) => {
  event.preventDefault();
  if (state.busy || !state.scan) return;
  try { state.query = readQuery(); queryPage(1); } catch (error) { message(error.message, 'error'); }
});
$('reset-filters').addEventListener('click', () => {
  if (state.busy || !state.scan) return;
  resetFilters();
  state.query = readQuery();
  queryPage(1);
});
$('add-filter').addEventListener('click', () => {
  if (state.busy || !state.scan || state.filterRules.length >= 20) return;
  state.filterRules.push(newFilterRule());
  renderFilterRules();
  updateControls();
});
$('field-search').addEventListener('input', renderTree);
$('select-all').addEventListener('click', () => { state.terminals.forEach((node) => state.selected.add(node.key)); selectionChanged(); });
$('select-none').addEventListener('click', () => { state.selected.clear(); selectionChanged(); });
$('select-defaults').addEventListener('click', selectDefaults);
$('collapse-tree').addEventListener('click', () => {
  state.nodes.forEach((node) => { if (node.children.length) state.collapsed.add(node.key); });
  $('field-search').value = '';
  renderTree();
});
$('table-tab').addEventListener('click', () => { state.view = 'table'; renderResults(); });
$('json-tab').addEventListener('click', () => { state.view = 'json'; renderResults(); });
$('page-size').addEventListener('change', () => {
  state.pageSize = Number($('page-size').value);
  if (state.scan && !state.busy) queryPage(1);
});
$('sort-order').addEventListener('change', () => {
  state.sort = $('sort-order').value;
  if (state.scan && !state.busy) queryPage(1);
});
$('previous-page').addEventListener('click', () => { if (!state.busy && state.result?.page > 1) queryPage(state.result.page - 1); });
$('next-page').addEventListener('click', () => { if (!state.busy && state.result?.page < state.result?.pages) queryPage(state.result.page + 1); });
$('export-button').addEventListener('click', exportCurrentPage);

async function initialize() {
  try { $('root-path').value = localStorage.getItem('officeace-directory') || ''; } catch {}
  updateControls();
  try {
    const status = await api('/api/status');
    if (!status.capabilities?.includes('field_filters')) {
      throw new Error('后台版本不支持任意字段筛选，请停止并重新运行 server.py，然后刷新页面。');
    }
    state.serviceReady = true;
    state.demoRoot = status.demo_root;
    if (!$('root-path').value && status.default_root) $('root-path').value = status.default_root;
    updateControls();
  } catch (error) { message(`无法连接本地数据服务：${error.message}`, 'error'); }
}

initialize();
