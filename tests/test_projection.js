'use strict';

// Dependency-free tests for the actual projection helpers in public/app.js.
// Run with: node --test tests/test_projection.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'public', 'app.js'), 'utf8');
const setupBoundary = source.indexOf("\n$('source-form').addEventListener");
assert.ok(setupBoundary > 0, 'App event wiring boundary must exist');

function loadHelpers() {
  const elements = new Map();
  const context = vm.createContext({
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, { value: '' });
        return elements.get(id);
      },
    },
  });
  // Skip only DOM event wiring and initialization; run the real helper source.
  vm.runInContext(source.slice(0, setupBoundary) + `
globalThis.helpers = {
  state, MISSING, pathKey, buildSchema, makeProjection, project, projectedRecords,
  readPath, valueText, displayPath,
};`, context, { filename: 'public/app.js' });
  return context.helpers;
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function select(api, fields, paths) {
  api.buildSchema(fields.map(([fieldPath, types]) => ({ path: fieldPath, types, count: 1 })));
  api.state.selected.clear();
  for (const selectedPath of paths) {
    const key = api.pathKey(selectedPath);
    assert.ok(api.state.terminals.some((node) => node.key === key), `Selectable path: ${key}`);
    api.state.selected.add(key);
  }
}

test('nested selection excludes siblings and distinguishes literal dotted keys', () => {
  const api = loadHelpers();
  select(api, [
    [['host'], ['object']], [['host', 'name'], ['string']],
    [['host', 'secret'], ['string']], [['host.name'], ['string']],
  ], [['host', 'name'], ['host.name']]);
  const record = { host: { name: 'nested', secret: 'private' }, 'host.name': 'literal', extra: 4 };
  const result = api.project(record, api.makeProjection());
  assert.deepEqual(plain(result), { host: { name: 'nested' }, 'host.name': 'literal' });
  assert.equal(api.readPath(record, ['host', 'name']), 'nested');
  assert.equal(api.readPath(record, ['host.name']), 'literal');
  assert.equal(api.displayPath(['host', 'name']), 'host.name');
  assert.equal(api.displayPath(['host.name']), '["host.name"]');
});

test('array projection preserves positions and omits unselected object properties', () => {
  const api = loadHelpers();
  select(api, [
    [['events'], ['array']], [['events', '[]'], ['object', 'null', 'number']],
    [['events', '[]', 'metadata'], ['object']],
    [['events', '[]', 'metadata', 'level'], ['string']],
  ], [['events', '[]', 'metadata', 'level']]);
  const record = { events: [
    { metadata: { level: 'info', secret: 'omit' }, id: 1 },
    { other: true }, null, 3,
    { metadata: { level: 'warn' } },
  ] };
  const result = api.project(record, api.makeProjection());
  assert.deepEqual(plain(result), { events: [
    { metadata: { level: 'info' } }, {}, null, null, { metadata: { level: 'warn' } },
  ] });
  const column = api.readPath(record, ['events', '[]', 'metadata', 'level']);
  assert.equal(api.valueText(column), '["info", —, —, —, "warn"]');
});

test('nested arrays retain both array levels including empty arrays', () => {
  const api = loadHelpers();
  select(api, [
    [['matrix'], ['array']], [['matrix', '[]'], ['array']],
    [['matrix', '[]', '[]'], ['object', 'null']],
    [['matrix', '[]', '[]', 'score'], ['number']],
  ], [['matrix', '[]', '[]', 'score']]);
  const record = { matrix: [[{ score: 1, secret: 2 }, { secret: 3 }], [], [null, { score: 4 }]] };
  assert.deepEqual(plain(api.project(record, api.makeProjection())), {
    matrix: [[{ score: 1 }, {}], [], [null, { score: 4 }]],
  });
});

test('missing fields are omitted while selected null, zero, false and empty strings survive', () => {
  const api = loadHelpers();
  select(api, [
    [['nullable'], ['null']], [['zero'], ['number']], [['disabled'], ['boolean']],
    [['empty'], ['string']], [['missing'], ['string']],
  ], [['nullable'], ['zero'], ['disabled'], ['empty'], ['missing']]);
  const result = api.project({ nullable: null, zero: 0, disabled: false, empty: '' }, api.makeProjection());
  assert.deepEqual(plain(result), { nullable: null, zero: 0, disabled: false, empty: '' });
  assert.equal(api.readPath({}, ['missing']), api.MISSING);
  assert.equal(api.valueText(null), 'null');
  assert.equal(api.valueText(''), '""');
  assert.equal(api.valueText(api.MISSING), '—');
});

test('mixed scalar and object parent selection never reveals unselected children', () => {
  const api = loadHelpers();
  select(api, [
    [['mixed'], ['object', 'string', 'null']],
    [['mixed', 'visible'], ['number']], [['mixed', 'private'], ['string']],
  ], [['mixed']]);
  const trie = api.makeProjection();
  assert.deepEqual(plain(api.project({ mixed: 'scalar' }, trie)), { mixed: 'scalar' });
  assert.deepEqual(plain(api.project({ mixed: null }, trie)), { mixed: null });
  assert.deepEqual(plain(api.project({ mixed: { visible: 1, private: 'secret' } }, trie)), { mixed: {} });
  api.state.selected.add(api.pathKey(['mixed', 'visible']));
  assert.deepEqual(plain(api.project({ mixed: { visible: 1, private: 'secret' } }, api.makeProjection())), {
    mixed: { visible: 1 },
  });
});

test('mixed scalar and object array elements do not reveal deselected descendants', () => {
  const api = loadHelpers();
  select(api, [
    [['values'], ['array']], [['values', '[]'], ['string', 'number', 'null', 'object']],
    [['values', '[]', 'private'], ['string']],
  ], [['values', '[]']]);
  const result = api.project({ values: ['text', 0, null, { private: 'secret' }] }, api.makeProjection());
  assert.deepEqual(plain(result), { values: ['text', 0, null, {}] });
});

test('parent terminal groups support choosing each depth and preserve empty containers', () => {
  const api = loadHelpers();
  api.buildSchema([
    { path: ['object'], types: ['object'] },
    { path: ['object', 'value'], types: ['string'] },
    { path: ['list'], types: ['array'] },
    { path: ['list', '[]'], types: ['object'] },
    { path: ['list', '[]', 'value'], types: ['number'] },
  ]);
  for (const fieldPath of [['object'], ['list'], ['list', '[]']]) {
    const node = api.state.nodes.get(api.pathKey(fieldPath));
    assert.ok(node.terminals.length > 0);
    node.terminals.forEach((key) => api.state.selected.add(key));
  }
  assert.deepEqual(plain(api.project({ object: {}, list: [] }, api.makeProjection())), { object: {}, list: [] });
  assert.deepEqual(plain(api.project({ object: { value: 'selected' }, list: [{ value: 1, secret: 2 }] }, api.makeProjection())), {
    object: { value: 'selected' }, list: [{ value: 1 }],
  });
});

test('prototype-like JSON keys remain own data without prototype pollution', () => {
  const api = loadHelpers();
  select(api, [
    [['__proto__'], ['object']], [['__proto__', 'polluted'], ['string']],
    [['constructor'], ['object']], [['constructor', 'prototype'], ['object']],
    [['constructor', 'prototype', 'selected'], ['boolean']],
  ], [['__proto__', 'polluted'], ['constructor', 'prototype', 'selected']]);
  const record = JSON.parse('{"__proto__":{"polluted":"data","secret":"omit"},"constructor":{"prototype":{"selected":true,"private":1}}}');
  const result = api.project(record, api.makeProjection());
  assert.equal(Object.getPrototypeOf(result), null);
  assert.equal(Object.prototype.polluted, undefined);
  assert.ok(Object.hasOwn(result, '__proto__'));
  assert.equal(result.__proto__.polluted, 'data');
  assert.equal(result.__proto__.secret, undefined);
  assert.deepEqual(plain(result.constructor), { prototype: { selected: true } });
  assert.equal(api.readPath(Object.create({ inherited: 'secret' }), ['inherited']), api.MISSING);
});

test('empty selection exports empty objects and never mutates source records', () => {
  const api = loadHelpers();
  const original = { host: { name: 'laptop', secret: 'private' }, values: [1, 2] };
  const snapshot = JSON.stringify(original);
  select(api, [[['host', 'name'], ['string']]], [['host', 'name']]);
  api.state.result = { items: [{ record: original }] };
  assert.deepEqual(plain(api.projectedRecords()), [{ host: { name: 'laptop' } }]);
  api.state.selected.clear();
  assert.deepEqual(plain(api.projectedRecords()), [{}]);
  assert.equal(JSON.stringify(original), snapshot);
});
