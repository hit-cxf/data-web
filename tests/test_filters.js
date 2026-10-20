'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'public', 'app.js'), 'utf8');
const boundary = source.indexOf("\n$('source-form').addEventListener");

function loadFilters() {
  const elements = new Map();
  const context = vm.createContext({ document: {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, { value: '' });
      return elements.get(id);
    },
  } });
  vm.runInContext(source.slice(0, boundary) + '\nglobalThis.api = {state, buildSchema, newFilterRule, readFieldFilters, readQuery};', context);
  const api = context.api;
  api.buildSchema([
    { path: ['start'], types: ['number'] },
    { path: ['resourceAttributes', 'host.name'], types: ['string'] },
    { path: ['resourceAttributes', 'host', 'name'], types: ['string'] },
    { path: ['events', '[]', 'user.id'], types: ['string'] },
  ]);
  return api;
}

const plain = (value) => JSON.parse(JSON.stringify(value));

test('query serializes exact arbitrary paths, independent of display field selection', () => {
  const api = loadFilters();
  api.state.filterRules = [
    { ...api.newFilterRule(['resourceAttributes', 'host.name']), value: ' Company-PC ' },
    { ...api.newFilterRule(['events', '[]', 'user.id']), operator: 'equals', value: 'employee' },
  ];
  api.state.selected.clear();
  assert.deepEqual(plain(api.readQuery()), {
    time_from: null, time_to: null,
    field_filters: [
      { path: ['resourceAttributes', 'host.name'], operator: 'contains', value: 'Company-PC' },
      { path: ['events', '[]', 'user.id'], operator: 'equals', value: 'employee' },
    ],
  });
});

test('empty conditions are ignored but an active condition requires a schema path', () => {
  const api = loadFilters();
  const rule = api.newFilterRule();
  api.state.filterRules = [rule];
  assert.deepEqual(plain(api.readFieldFilters()), []);
  rule.value = 'copied-host';
  assert.throws(() => api.readFieldFilters(), /选择完整/);
  rule.path = ['unknown'];
  assert.throws(() => api.readFieldFilters(), /选择完整/);
});

test('arbitrary numeric or timestamp ranges include only the active operator inputs', () => {
  const api = loadFilters();
  api.state.filterRules = [{
    ...api.newFilterRule(['start']), operator: 'between', from: ' 9 ', to: ' 11 ', value: 'old-keyword',
  }];
  assert.deepEqual(plain(api.readFieldFilters()), [
    { path: ['start'], operator: 'between', from: '9', to: '11' },
  ]);
});
