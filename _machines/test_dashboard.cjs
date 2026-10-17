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
const cacheKey = 'machines.latest.v1';

class Element {
  constructor() { this.dataset = {}; this.style = {}; this.textContent = ''; this.listeners = {}; }
  append() {}
  replaceChildren() {}
  setAttribute() {}
  addEventListener(name, listener) { this.listeners[name] = listener; }
  closest() { return this; }
}

function boot({fail = [], hang = [], bodyHang = [], hold = [], data = payload(), byRoute = {}, cache = new Map(), storageBlocked = false} = {}) {
  const nodes = new Map(), calls = [], timers = new Map(), intervals = [], releases = new Map();
  let timerId = 0, elapsed = 0;
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  node('machines-inventory').textContent = JSON.stringify(inventory);
  const context = {
    window: {MACHINES_CONFIG: {statusUrl: primary, statusUrls: [primary, fallback], refreshMs: 120000, staleAfterSeconds: 300}},
    document: {getElementById: node, createElement: () => new Element(), addEventListener() {}, hidden: false},
    performance: {now: () => elapsed}, Intl, Date, Map, Set, Number, Math, String, Boolean, AbortController,
    localStorage: {
      getItem: key => { if (storageBlocked) throw new Error('Storage blocked'); return cache.get(key) || null; },
      setItem: (key, value) => { if (storageBlocked) throw new Error('Storage blocked'); cache.set(key, value); }
    },
    setTimeout: fn => { timers.set(++timerId, fn); return timerId; }, clearTimeout: id => timers.delete(id),
    setInterval: (fn, ms) => intervals.push({fn, ms}),
    fetch: async url => {
      calls.push(url);
      if (url.startsWith('/assets/')) return {ok: true, json: async () => inventory};
      // Deliberately ignore AbortSignal to reproduce a stalled browser transport.
      if (hang.includes(url)) return new Promise(() => {});
      if (fail.includes(url)) throw new TypeError('Route unavailable');
      const response = {ok: true, json: async () => bodyHang.includes(url) ? new Promise(() => {}) : (byRoute[url] || data)};
      if (hold.includes(url)) return new Promise(resolve => releases.set(url, () => resolve(response)));
      return response;
    }
  };
  vm.runInNewContext(source, context);
  return {node, calls, timers, intervals, releases, cache,
    counts: () => Object.fromEntries(['total', 'free', 'used', 'unknown'].map(key => [key, node(`summary-${key}`).textContent])),
    expire: () => { elapsed = 301000; intervals[1].fn(); },
    timeOut: () => { for (const fn of [...timers.values()]) fn(); }};
}

(async () => {
  let test = boot({hang: [primary]});
  await flush();
  assert.deepEqual(test.calls, [primary, fallback]);
  assert.deepEqual(test.counts(), {total: '8', free: '8', used: '0', unknown: '0'}, 'A healthy route must render without waiting for a stalled route');
  assert.equal(test.node('refresh-machines').disabled, false);
  assert.equal(test.intervals[0].ms, 120000);
  assert.equal(test.cache.size, 1, 'Only one latest snapshot is saved');

  for (const options of [{hang: [primary, fallback]}, {bodyHang: [primary, fallback]}]) {
    test = boot(options);
    await flush(); test.timeOut(); await flush();
    assert.deepEqual(test.counts(), {total: '8', free: '—', used: '—', unknown: '—'});
    assert.equal(test.node('refresh-machines').disabled, false, 'Even an abort-ignoring transport or body read must release the refresh button');
    assert.match(test.node('machines-message').textContent, /8 秒内未能获取/);
  }

  const stale = payload();
  stale.machines.forEach(machine => { machine.status = 'stale'; machine.age_seconds = 600; });
  test = boot({hold: [primary], byRoute: {[fallback]: stale}});
  await flush();
  assert.equal(test.counts().free, '—', 'A fast stale response must not win over a fresh response still in flight');
  test.releases.get(primary)(); await flush();
  assert.equal(test.counts().free, '8');

  const cached = new Map([[cacheKey, JSON.stringify({savedAt: Date.now() - 60000, payload: payload()})]]);
  test = boot({hang: [primary, fallback], cache: cached});
  assert.equal(test.counts().free, '8', 'Cached data renders synchronously before network callbacks');
  assert.match(test.node('machines-message').textContent, /上次成功获取/);
  test.timeOut(); await flush();
  assert.equal(test.counts().free, '8');
  assert.match(test.node('machines-message').textContent, /显示上次数据/);
  test.expire(); assert.equal(test.counts().free, '0'); assert.equal(test.counts().unknown, '8');

  cached.set(cacheKey, JSON.stringify({savedAt: Date.now() - 3600000, payload: payload()}));
  test = boot({hang: [primary, fallback], cache: cached});
  assert.equal(test.counts().free, '0', 'Old cache must never imply free GPUs');
  assert.equal(test.counts().unknown, '8');
  test = boot({storageBlocked: true}); await flush(); assert.equal(test.counts().free, '8');

  const metrics = payload();
  metrics.machines[0].telemetry.gpus = [card(0, 9.9, 1023), card(1, 10), card(2, 0, 1024), {index: null}];
  metrics.machines[1].status = 'stale'; metrics.machines[1].age_seconds = 600;
  test = boot({data: metrics}); await flush();
  assert.deepEqual(test.counts(), {total: '8', free: '1', used: '2', unknown: '5'});
  test.expire(); assert.deepEqual(test.counts(), {total: '8', free: '0', used: '0', unknown: '8'});
  console.log('Dashboard checks passed: parallel routes, strict header/body deadlines, fresh-over-stale selection, immediate cache display, expiry, blocked storage, and two-minute polling.');
})().catch(error => { console.error(error); process.exitCode = 1; });
