const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../assets/js/machines.js'), 'utf8');
const primary = 'https://primary.test/status', fallback = 'https://fallback.test/status';
const inventory = ['10.20.6.38', '10.20.5.8'].map(id => ({id, gpu_count: 4, gpu_model: 'RTX 3090'}));
const card = (index, utilization_pct = 0, memory_used_mib = 20) => ({index, utilization_pct, memory_used_mib, memory_total_mib: 24576});
const payload = () => ({schema_version: 1, machines: inventory.map(item => ({id: item.id, status: 'online', age_seconds: 0, telemetry: {gpus: [0, 1, 2, 3].map(index => card(index))}}))});
const flush = () => new Promise(setImmediate);

class Element {
  constructor() { this.dataset = {}; this.style = {}; this.textContent = ''; this.listeners = {}; }
  append() {}
  replaceChildren() {}
  setAttribute() {}
  addEventListener(name, listener) { this.listeners[name] = listener; }
  closest() { return this; }
}

function boot({fail = [], hangPrimary = false, data = payload()} = {}) {
  const nodes = new Map(), calls = [], timers = new Map(), intervals = [];
  let timerId = 0, elapsed = 0;
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const context = {
    window: {MACHINES_CONFIG: {statusUrl: primary, statusUrls: [primary, fallback]}},
    document: {getElementById: node, createElement: () => new Element(), addEventListener() {}, hidden: false},
    performance: {now: () => elapsed}, Intl, Date, Map, Set, Number, Math, String, Boolean, AbortController,
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id),
    setInterval: fn => intervals.push(fn),
    fetch: async (url, options) => {
      if (url.startsWith('/assets/')) return {ok: true, json: async () => inventory};
      calls.push(url);
      if (url === primary && hangPrimary) return new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new Error('Aborted'))));
      if (fail.includes(url)) throw new TypeError('Route unavailable');
      return {ok: true, json: async () => data};
    }
  };
  vm.runInNewContext(source, context);
  return {node, calls, timers,
    counts: () => Object.fromEntries(['total', 'free', 'used', 'unknown'].map(key => [key, node(`summary-${key}`).textContent])),
    expire: () => { elapsed = 121000; intervals[1](); }};
}

(async () => {
  let test = boot({fail: [primary]});
  await flush();
  assert.deepEqual(test.calls, [primary, fallback]);
  assert.deepEqual(test.counts(), {total: '8', free: '8', used: '0', unknown: '0'});
  await test.node('refresh-machines').listeners.click();
  assert.deepEqual(test.calls, [primary, fallback, fallback], 'Reuse the successful network route');

  test = boot({fail: [primary, fallback]});
  await flush();
  assert.deepEqual(test.counts(), {total: '8', free: '—', used: '—', unknown: '—'});
  assert.match(test.node('machines-message').textContent, /不代表显卡故障/);

  test = boot({hangPrimary: true});
  await flush();
  test.timers.values().next().value();
  await flush();
  assert.deepEqual(test.calls, [primary, fallback], 'A stalled route must time out and fall back');
  assert.equal(test.counts().free, '8');

  const metrics = payload();
  metrics.machines[0].telemetry.gpus = [card(0, 9.9, 1023), card(1, 10), card(2, 0, 1024), {index: null}];
  metrics.machines[1].status = 'stale';
  test = boot({data: metrics});
  await flush();
  assert.deepEqual(test.counts(), {total: '8', free: '1', used: '2', unknown: '5'});
  test.expire();
  assert.deepEqual(test.counts(), {total: '8', free: '0', used: '0', unknown: '8'});
  console.log('Dashboard checks passed: network fallback, route reuse, timeouts, connection errors, availability thresholds, and stale samples.');
})().catch(error => { console.error(error); process.exitCode = 1; });
