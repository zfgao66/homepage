const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync(path.join(__dirname, '../assets/js/machines-management.js'), 'utf8');
const flush = () => new Promise(setImmediate);
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.dataset = {}; this.style = {}; this.textContent = ''; this.children = []; this.listeners = {}; this.value = ''; this.disabled = false; this.hidden = false; this.checked = false; this.open = false; }
  append(...items) { items.forEach(item => { item.parent = this; this.children.push(item); }); }
  replaceChildren(...items) { this.children.forEach(child => {child.parent = null;}); this.children = []; this.append(...items); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
  after(item) { const siblings = this.parent.children, index = siblings.indexOf(this); item.parent = this.parent; siblings.splice(index + 1, 0, item); }
  contains(item) { return this === item || this.children.some(child => child.contains(item)); }
  querySelectorAll(selector) {
    const options = selector.split(',').map(part => part.trim());
    const matches = node => options.some(option => option.startsWith('.') ? (node.className || '').split(' ').includes(option.slice(1)) : option === 'details[open]' ? node.tagName === 'details' && node.open : node.tagName === option);
    const all = []; const visit = node => node.children.forEach(child => { if (matches(child)) all.push(child); visit(child); }); visit(this); return all;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
  scrollIntoView() {}
}
(async () => {
  const nodes = new Map(), pending = [], calls = [];
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  const machine = {id: '10.20.1.21', gpu_model: 'H100', gpu_count: 8};
  node('machines-inventory').textContent = JSON.stringify([machine]);
  node('machines-initial-session').textContent = JSON.stringify({authenticated: true, csrf_token: 'test-csrf'});
  node('statistics-days').value = '7';
  const monitorRow = new Element('article'); monitorRow.dataset.machineId = machine.id; node('machines-list').append(monitorRow);
  let stored = [];
  const context = {
    window: {MachinesMonitor: {start() {}, stop() {}}, addEventListener() {}},
    document: {getElementById: node, createElement: tag => new Element(tag), addEventListener() {}, hidden: false,
      querySelectorAll: selector => selector === '#machines-list > article[data-machine-id]' ? node('machines-list').children.filter(child => child.dataset.machineId) : []},
    Date, Intl, JSON, Set, Map, Math, Number, String, Boolean, Error, AbortController, crypto: webcrypto, confirm: () => { throw new Error('Native confirmation dialogs must not be used'); },
    navigator: {clipboard: {writeText: async () => {}}}, setTimeout, clearTimeout, setInterval() {},
    fetch: (url, options) => {
      calls.push({url, options});
      if (options.method !== 'GET') return new Promise(resolve => pending.push({url, options, resolve}));
      const data = url.includes('/registrations?') ? {registrations: stored} : url.includes('/overview') ? {active_gpu_count: 0, long_term_count: 0, expiring_count: 0} : {days: 7, people: [], total_gpu_hours: 0};
      return Promise.resolve({ok: true, status: 200, json: async () => data});
    }
  };
  vm.runInNewContext(source, context); await flush();
  const open = () => {
    const apply = node('machines-list').querySelectorAll('button').find(button => button.textContent === '申请使用 GPU'); apply.listeners.click();
    return node('registration-dialog-content').querySelector('form');
  };
  const input = (form, name) => form.querySelectorAll('input, select, textarea').find(item => item.name === name);
  const fill = (form, name) => {input(form, 'name').value = name; input(form, 'wechat').value = `${name}_wechat`; input(form, 'server_username').value = 'wkli';};
  const submit = form => form.listeners.submit({preventDefault() {}});
  const reply = (request, data) => request.resolve({ok: true, status: 200, json: async () => data});

  // Closing a pending preview must not submit its old snapshot or disturb the replacement form.
  const first = open(); fill(first, 'First'); const firstSubmission = submit(first);
  assert.equal(input(first, 'name').disabled, true);
  assert.equal(input(first, 'end_at').disabled, true);
  assert.equal(input(first, 'gpu_count').disabled, true);
  const firstPreview = pending.shift(); assert.match(firstPreview.url, /\/preview$/);
  node('registration-dialog-close').listeners.click(); const replacement = open(); fill(replacement, 'Replacement');
  reply(firstPreview, {conflicts: []}); await firstSubmission; await flush();
  assert.equal(pending.length, 0, 'A closed preview must never continue into a create request');
  assert.equal(node('registration-dialog').open, true);
  assert.equal(node('registration-dialog-content').contains(replacement), true);
  assert.equal(input(replacement, 'name').value, 'Replacement');
  assert.equal(input(first, 'name').disabled, false, 'Only the original controls are restored');

  // A save already sent may finish, but must not close a newly opened dialog.
  const secondSubmission = submit(replacement); const secondPreview = pending.shift(); reply(secondPreview, {conflicts: []}); await flush();
  const save = pending.shift(); assert.match(save.url, /\/registrations$/);
  const payload = JSON.parse(save.options.body);
  node('registration-dialog-close').listeners.click(); const newest = open(); fill(newest, 'Unsaved newest');
  const saved = {...payload, id: 'saved-record', version: 1, status: 'active', long_term: false, continuous_days: 1, planned_gpu_hours: 24, conflicts: []}; stored = [saved];
  reply(save, {registration: saved}); await secondSubmission; await flush();
  assert.equal(node('registration-dialog').open, true, 'The older save cannot close a newer dialog');
  assert.equal(node('registration-dialog-content').contains(newest), true);
  assert.equal(input(newest, 'name').value, 'Unsaved newest');
  assert.equal(input(newest, 'name').disabled, false);
  assert.equal(node('machines-list').querySelectorAll('.registration-card').length, 1, 'The completed save is still displayed immediately');

  // Editing requires a separate, explicit in-page acknowledgement; changing inputs resets it.
  node('registration-dialog-close').listeners.click();
  node('machines-list').querySelectorAll('button').find(button => button.textContent === '编辑').listeners.click();
  const editForm = node('registration-dialog-content').querySelector('form'), sharedConfirmation = input(editForm, 'confirm_shared_change');
  assert.equal(sharedConfirmation.required, true); assert.equal(sharedConfirmation.checked, false);
  await submit(editForm); assert.equal(pending.length, 0, 'An unconfirmed shared edit must not make a preview or save request');
  sharedConfirmation.checked = true; input(editForm, 'notes').value = 'Updated safely';
  editForm.listeners.input({target: input(editForm, 'notes')});
  assert.equal(sharedConfirmation.checked, false, 'Changing a confirmed plan requires a fresh acknowledgement');
  sharedConfirmation.checked = true; const editSubmission = submit(editForm);
  reply(pending.shift(), {conflicts: []}); await flush();
  const editRequest = pending.shift(); assert.equal(editRequest.options.method, 'PATCH');
  const edited = {...saved, ...JSON.parse(editRequest.options.body), version: 2}; stored = [edited];
  reply(editRequest, {registration: edited}); await editSubmission; await flush();

  // Ending requires the in-page action button, and its late result also preserves a newer form.
  node('machines-list').querySelectorAll('button').find(button => button.textContent === '提前结束').listeners.click();
  assert.equal(pending.length, 0, 'Opening the end confirmation must not modify a record');
  const endButton = node('registration-dialog-content').querySelectorAll('button').find(button => button.textContent === '确认结束登记');
  const endPromise = endButton.listeners.click(); const endRequest = pending.shift(); assert.match(endRequest.url, /\/end$/);
  assert.equal(endButton.disabled, true);
  node('registration-dialog-close').listeners.click(); const afterEndForm = open(); fill(afterEndForm, 'Keep this new form');
  const ended = {...edited, status: 'ended', version: 3}; stored = [ended];
  reply(endRequest, {registration: ended}); await endPromise; await flush();
  assert.equal(node('registration-dialog').open, true);
  assert.equal(node('registration-dialog-content').contains(afterEndForm), true);
  assert.equal(input(afterEndForm, 'name').value, 'Keep this new form');

  // Cancelling a future booking also requires its own explicit action button.
  const scheduled = {...saved, id: 'future-record', status: 'scheduled'}; stored.push(scheduled);
  await node('refresh-registrations').listeners.click(); await flush();
  node('registration-dialog-close').listeners.click();
  node('machines-list').querySelectorAll('button').find(button => button.textContent === '取消预约').listeners.click();
  assert.equal(pending.length, 0);
  const cancelPromise = node('registration-dialog-content').querySelectorAll('button').find(button => button.textContent === '确认取消预约').listeners.click();
  const cancelRequest = pending.shift(); assert.match(cancelRequest.url, /\/cancel$/);
  const cancelled = {...scheduled, status: 'cancelled', version: 2}; stored = [ended, cancelled];
  reply(cancelRequest, {registration: cancelled}); await cancelPromise; await flush();
  const html = fs.readFileSync(path.join(__dirname, '../_includes/machines-content.html'), 'utf8');
  assert.match(html, /id="machines-password"[^>]*maxlength="128"/, 'Login input limit matches the backend 128-character limit');
  console.log('Management race checks passed: frozen form snapshots, dialog-generation guards, in-page shared-edit/end/cancel confirmations, and backend login input limit.');
})().catch(error => {console.error(error); process.exitCode = 1;});
