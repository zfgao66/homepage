const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../assets/js/machines-frame.js'), 'utf8');
function boot() {
  const events = {}, messages = {}, timers = [];
  const frame = {style: {}, contentWindow: {postMessage() {}}, addEventListener: (type, fn) => { messages[type] = fn; }};
  const view = {dataset: {}, hidden: true, remove() { this.removed = true; }};
  const standard = {hidden: false}, help = {hidden: true};
  const nodes = {'machines-relay-frame': frame, 'machines-relay-view': view, 'machines-standard': standard, 'machines-connection-help': help};
  const window = {addEventListener: (type, fn) => { messages[type] = fn; }};
  vm.runInNewContext(source, {window, document: {getElementById: id => nodes[id], addEventListener: (type, fn) => { events[type] = fn; }}, Number, Math, setTimeout: fn => timers.push(fn)});
  return {window, frame, view, standard, help, events, messages, timers,
    reply: (options = {}) => messages.message({origin: 'https://43.155.209.84', source: frame.contentWindow, data: {type: 'machines-view-v1', ready: true, height: 2400}, ...options})};
}
let test = boot();
test.events['machines:status']({detail: {fresh: true, fromCache: true}});
assert.equal(test.view.removed, undefined, 'A cached result must not prevent a working page transport');
test.reply({origin: 'https://unrelated.test'}); assert.equal(test.standard.hidden, false);
test.reply({source: {}}); assert.equal(test.standard.hidden, false);
test.reply(); assert.equal(test.standard.hidden, true); assert.equal(test.view.dataset.pending, 'false');
assert.equal(test.frame.style.height, '2400px'); assert.equal(test.window.MACHINES_PAUSED, true);
test.events['machines:status']({detail: {fresh: true, fromCache: false}});
assert.equal(test.view.removed, undefined, 'A later API result must not remove the selected frame');
test = boot(); test.events['machines:status']({detail: {fresh: true, fromCache: false}});
assert.equal(test.view.removed, true, 'A fast fresh API should keep the existing working desktop view');
test.reply(); assert.equal(test.standard.hidden, false);
test = boot(); test.timers[0](); assert.equal(test.help.hidden, false);
const bridge = fs.readFileSync(path.join(__dirname, 'dashboard_bridge.js'), 'utf8');
const bridgeEvents = {}, bridgeWindowEvents = {}, replies = [];
const parent = {postMessage: (...args) => replies.push(args)};
vm.runInNewContext(bridge, {
  window: {parent, addEventListener: (type, fn) => { bridgeWindowEvents[type] = fn; }},
  document: {body: {scrollHeight: 2400}, addEventListener: (type, fn) => { bridgeEvents[type] = fn; }},
  ResizeObserver: class { observe() {} }, Math, Boolean
});
bridgeEvents['machines:status']({detail: {fresh: true, fromCache: false}});
assert.equal(replies.length, 0, 'No status message is sent before a trusted parent handshake');
bridgeWindowEvents.message({source: parent, origin: 'https://unrelated.test', data: {type: 'machines-parent-v1'}});
assert.equal(replies.length, 0);
bridgeWindowEvents.message({source: parent, origin: 'http://gaozefeng.tech', data: {type: 'machines-parent-v1'}});
assert.equal(replies[0][0].ready, true); assert.equal(replies[0][1], 'http://gaozefeng.tech');
bridgeEvents['machines:status']({detail: {fresh: false, fromCache: false}});
assert.equal(replies.at(-1)[0].ready, false, 'A stale page cannot displace a fresh JSON route');
console.log('Frame checks passed: first working view, cache does not lock the route, trusted source only, resizing, and direct-access fallback.');
