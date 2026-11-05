const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const source = fs.readFileSync(path.join(__dirname, '../assets/js/machines-management.js'), 'utf8');
if (process.argv[2] === '--timezone-child') {
  const context = {window: {}, document: {getElementById: () => null}, Date};
  vm.runInNewContext(source, context);
  const utils = context.window.MachinesManagementUtils;
  assert.equal(utils.beijingValue('2026-10-31T16:00:00Z'), '2026-11-01T00:00');
  assert.equal(utils.beijingISO('2026-11-01T00:00'), '2026-10-31T16:00:00.000Z');
  assert.equal(utils.beijingISO('2026-03-08T02:30'), '2026-03-07T18:30:00.000Z', 'Beijing input must not use the device daylight-saving rules');
  assert.throws(() => utils.beijingISO('2026-02-30T12:00'), /无效/);
  assert.throws(() => utils.beijingISO('2026-11-01'), /完整/);
  assert.equal(utils.isLongTerm('2026-11-01T00:00:00+08:00', '2026-11-08T00:00:00+08:00'), false);
  assert.equal(utils.isLongTerm('2026-11-01T00:00:00+08:00', '2026-11-08T00:00:01+08:00'), true);
  assert.equal(utils.formISO('2026-11-05T22:39', '2026-11-05T14:39:57.489165Z'), '2026-11-05T14:39:57.489165Z', 'Metadata edits preserve server start time including seconds');
  const start = utils.beijingISO('2026-11-01T00:00'), end = utils.beijingISO('2026-12-01T00:00');
  assert.equal((Date.parse(end) - Date.parse(start)) / 3600000, 720, 'A month-long registration must retain the entire period');
  process.exit(0);
}
for (const timezone of ['UTC', 'America/Los_Angeles', 'Asia/Shanghai']) {
  const result = spawnSync(process.execPath, [__filename, '--timezone-child'], {encoding: 'utf8', env: {...process.env, TZ: timezone}});
  assert.equal(result.status, 0, `${timezone}: ${result.stderr}`);
}

const monitorSource = fs.readFileSync(path.join(__dirname, '../assets/js/machines.js'), 'utf8');
class Element {
  constructor() { this.dataset = {}; this.style = {}; this.textContent = ''; this.listeners = {}; this.childNodes = []; }
  append(...children) { this.childNodes.push(...children); }
  replaceChildren(...children) { this.childNodes = children; }
  setAttribute() {}
  addEventListener(name, callback) { this.listeners[name] = callback; }
  closest() { return this; }
}
(async () => {
  const nodes = new Map(), calls = [], cache = new Map(), events = [];
  let resolveFetch;
  const getNode = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  getNode('machines-inventory').textContent = JSON.stringify([{id: '10.20.1.21', gpu_count: 8, gpu_model: 'H100'}]);
  const context = {
    window: {MACHINES_CONFIG: {requireAuth: true, statusUrl: '/machines-api/status', refreshMs: 120000}},
    document: {getElementById: getNode, createElement: () => new Element(), addEventListener() {}, dispatchEvent: event => events.push(event), hidden: false},
    CustomEvent: class {constructor(type, options) {this.type = type; this.detail = options?.detail;}},
    performance: {now: () => 0}, Intl, Date, Map, Set, Number, Math, String, Boolean, AbortController,
    localStorage: {getItem: key => cache.get(key) || null, setItem: (key, value) => cache.set(key, value), removeItem: key => cache.delete(key)},
    setTimeout, clearTimeout, setInterval() {},
    fetch: (url, options) => {calls.push({url, options}); return new Promise(resolve => {resolveFetch = resolve;});}
  };
  vm.runInNewContext(monitorSource, context);
  assert.equal(calls.length, 0, 'A logged-out page must not fetch protected monitoring data');
  context.window.MachinesMonitor.start();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(getNode('summary-total').textContent, '8', 'The H100 inventory supports all eight cards');
  const monitorRow = getNode('machines-list').childNodes[0], registrationRow = new Element();
  getNode('machines-list').append(registrationRow);
  cache.set('machines.latest.v1', 'private snapshot');
  context.window.MachinesMonitor.stop();
  assert.equal(cache.size, 0, 'Logout removes the monitoring snapshot');
  assert.equal(getNode('machines-list').childNodes.length, 0, 'Logout removes private server and registration content');
  resolveFetch({ok: true, json: async () => ({schema_version: 1, machines: [{id: '10.20.1.21', status: 'online', age_seconds: 0, telemetry: {gpus: []}}]})});
  await new Promise(setImmediate);
  assert.equal(cache.size, 0, 'A late authenticated response must not repopulate the cache after logout');
  assert.equal(getNode('machines-list').childNodes.length, 0);
  assert.equal(monitorRow.dataset.machineId, '10.20.1.21');
  console.log('Management checks passed: Beijing times across device zones, month duration, long-term boundary, authentication gate, same-origin session, and logout/late-response cleanup.');
})().catch(error => {console.error(error); process.exitCode = 1;});
