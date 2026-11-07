(() => {
  'use strict';
  const beijingValue = value => new Date(new Date(value).getTime() + 8 * 3600000).toISOString().slice(0, 16);
  const beijingISO = value => {
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) throw new Error('请填写完整的北京时间。');
    const date = new Date(`${value}:00+08:00`);
    if (!Number.isFinite(date.getTime()) || beijingValue(date) !== value) throw new Error('日期或时间无效，请重新选择。');
    return date.toISOString();
  };
  const formISO = (value, original) => original && value === beijingValue(original) ? original : beijingISO(value);
  const isLongTerm = (start, end) => new Date(end) - new Date(start) > 7 * 86400000;
  const normalizeUsernames = value => {
    if (typeof value !== 'string' || value.length > 649) throw new Error('最多填写 10 个服务器账号，每个账号不超过 64 个字符。');
    const names = value.replace(/^ +| +$/g, '').split(/ +/);
    if (names.some(name => !/^[A-Za-z_][A-Za-z0-9_.@-]{0,63}(?![\s\S])/.test(name))) throw new Error('请只填写 SSH 账号名，多个账号用英文空格分隔，例如 zfgao wkli；不要填写逗号、中文、密码或换行。');
    if (names.length > 10) throw new Error('最多填写 10 个服务器账号。');
    if (new Set(names).size !== names.length) throw new Error('服务器账号不能重复填写。');
    return names.join(' ');
  };
  window.MachinesManagementUtils = Object.freeze({beijingValue, beijingISO, isLongTerm, formISO, normalizeUsernames});
  const $ = id => document.getElementById(id);
  if (!$('machines-login-form')) return;
  const apiRoot = '/machines-api', statuses = {active: '登记使用中', scheduled: '即将开始', expired: '已到期', ended: '已提前结束', cancelled: '已取消'};
  const dateFormatter = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false});
  const formatDate = value => value && Number.isFinite(Date.parse(value)) ? dateFormatter.format(new Date(value)) : '—';
  const decimal = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString('zh-CN', {maximumFractionDigits: 1}) : '—';
  let authenticated = false, csrf = '', records = [], overview = null, inventory = [], loading = false, sessionGeneration = 0, dialogGeneration = 0, registrationRevision = 0, refreshPending = false;
  const requests = new Set(), registrationRows = new Map();
  try { inventory = JSON.parse($('machines-inventory')?.textContent || '[]'); } catch { /* The login remains usable when inventory is malformed. */ }
  const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
  const button = (text, action, className = '') => { const node = el('button', `management-button ${className}`, text); node.type = 'button'; node.addEventListener('click', action); return node; };
  const message = (target, text, warning = false) => { target.textContent = text; target.dataset.warning = String(warning); };
  const uid = () => crypto.randomUUID();
  const active = record => ['active', 'scheduled'].includes(record.status);
  const sortRecords = list => [...list].sort((a, b) => ({active: 0, scheduled: 1}[a.status] ?? 2) - ({active: 0, scheduled: 1}[b.status] ?? 2) || Date.parse(a.start_at) - Date.parse(b.start_at));

  function lock(text = '') {
    authenticated = false; csrf = ''; sessionGeneration++; loading = false; refreshPending = false;
    requests.forEach(controller => controller.abort()); requests.clear();
    window.MachinesMonitor?.stop(); records = []; overview = null; registrationRows.clear();
    $('machines-app').hidden = true; $('machines-login').hidden = false;
    if ($('registration-dialog').open) $('registration-dialog').close();
    $('registration-dialog-content').replaceChildren(); $('history-results').replaceChildren(); $('statistics-results').replaceChildren();
    ['registration-active-cards', 'registration-long-term', 'registration-expiring'].forEach(id => { $(id).textContent = '—'; });
    if ($('machines-initial-session')) $('machines-initial-session').textContent = '{"authenticated":false}';
    message($('machines-login-message'), text, Boolean(text));
    $('machines-password').value = '';
  }
  async function api(path, {method = 'GET', body, key, login = false} = {}) {
    const controller = new AbortController(), capturedGeneration = sessionGeneration;
    const timeout = setTimeout(() => controller.abort(), 12000); requests.add(controller);
    try {
      const headers = {Accept: 'application/json'};
      if (method !== 'GET') { headers['Content-Type'] = 'application/json'; if (!login) headers['X-CSRF-Token'] = csrf; }
      if (key) headers['Idempotency-Key'] = key;
      const response = await fetch(`${apiRoot}${path}`, {method, credentials: 'same-origin', cache: 'no-store', headers, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal});
      let data; try { data = await response.json(); } catch { throw new Error('服务器返回了无法读取的内容，请稍后刷新。'); }
      if (capturedGeneration !== sessionGeneration) throw new Error('访问会话已变更，请重试。');
      if (response.status === 401 && !login) { lock('访问会话已结束，请重新输入课题组密码。'); throw new Error('请重新登录。'); }
      if (!response.ok) { const error = new Error(data.error || '操作未完成，请重试。'); Object.assign(error, {status: response.status, code: data.code, details: data.details || {}}); throw error; }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('连接超时或已取消。提交结果可能尚未返回，请保持表单并重试，避免重新创建相同登记。');
      throw error;
    } finally { clearTimeout(timeout); requests.delete(controller); }
  }
  function enter(session) {
    if (!session?.authenticated || typeof session.csrf_token !== 'string') { lock(); return; }
    authenticated = true; csrf = session.csrf_token;
    $('machines-login').hidden = true; $('machines-app').hidden = false; $('machines-password').value = '';
    window.MachinesMonitor?.start(); ensureRegistrationRows(); refreshAll();
  }
  $('machines-login-form').addEventListener('submit', async event => {
    event.preventDefault(); const submit = event.currentTarget.querySelector('button'); submit.disabled = true;
    message($('machines-login-message'), '正在验证…');
    try { const data = await api('/login', {method: 'POST', body: {password: $('machines-password').value}, login: true}); enter(data); }
    catch (error) { message($('machines-login-message'), error.message, true); }
    finally { submit.disabled = false; }
  });
  $('machines-logout').addEventListener('click', async () => {
    const target = $('machines-logout'); target.disabled = true;
    try { await api('/logout', {method: 'POST', body: {}}); lock('已退出。'); }
    catch (error) { if (authenticated) message($('registrations-message'), `退出尚未完成：${error.message}`, true); }
    finally { target.disabled = false; }
  });
  document.addEventListener('machines:session-expired', () => lock('访问会话已结束，请重新输入课题组密码。'));
  window.addEventListener('pageshow', async event => {
    if (!event.persisted) return;
    lock();
    try { enter(await api('/session')); } catch { /* A missing session keeps the login visible. */ }
  });
  document.addEventListener('machines:monitor-render', ensureRegistrationRows);

  async function copyWechat(value, target) {
    try {
      await navigator.clipboard.writeText(value); const previous = target.textContent; target.textContent = '已复制'; setTimeout(() => { if (target.isConnected) target.textContent = previous; }, 2000);
    } catch {
      const field = el('input', 'wechat-copy-fallback'); field.readOnly = true; field.value = value; field.setAttribute('aria-label', '请长按或使用复制快捷键复制微信号');
      target.after(field); field.focus(); field.select(); target.textContent = '请手动复制微信号';
    }
  }
  function contact(record) {
    const line = el('div', 'registration-contact'); line.append(el('span', '', `微信：${record.wechat || '待补充'}`));
    if (!record.wechat) return line;
    const copy = button('复制微信', () => copyWechat(record.wechat, copy)); line.append(copy); return line;
  }
  function description(label, text, long = false) {
    if (!text) return null;
    if (long && text.length > 100) {
      const details = el('details', 'registration-notes'); details.append(el('summary', '', `${label}：${text.slice(0, 80)}…（展开全文）`), el('p', '', text)); return details;
    }
    return el('p', 'registration-description', `${label}：${text}`);
  }
  function usagePanel(usage) {
    const panel = el('div', 'registration-usage');
    panel.append(el('strong', '', '实际使用统计（采样估算）'));
    if (!usage || usage.account_status === 'missing') {
      panel.append(el('p', '', '请编辑登记，补充服务器账号后开始统计；历史缺失时间不计为空闲。'));
      return panel;
    }
    if (usage.account_status === 'pending') panel.append(el('p', '', '账号待核验：服务器暂不可达或清单未更新，全部账号核验后自动开始合并统计。'));
    const percent = value => value == null ? '待采集' : `${decimal(value)}%`;
    panel.append(el('p', '', `已登记 ${decimal(usage.elapsed_registered_gpu_hours)} GPU 小时 · 有数据覆盖 ${decimal(usage.observed_registered_gpu_hours)} GPU 小时`));
    panel.append(el('p', '', `实际占用 ${usage.occupied_gpu_hours == null ? '待采集' : decimal(usage.occupied_gpu_hours) + ' GPU 小时'} · 登记使用率 ${percent(usage.usage_percent)} · 数据覆盖率 ${percent(usage.coverage_percent)}`));
    panel.append(el('p', 'management-muted', '覆盖率是登记期间采样完整的比例，缺失时间不计为空闲。使用率只按有数据的时间计算；多个账号合并，同卡同时占用只计一次。占用含等待/显存驻留，不代表计算效率。历史采样保留 35 天。'));
    return panel;
  }
  function recordCard(record, history = false) {
    const card = el('article', 'registration-card'); card.dataset.recordId = record.id;
    const heading = el('div', 'registration-card-heading'); heading.append(el('h4', '', record.name));
    const badges = el('div', 'registration-badges'); const state = el('span', 'registration-badge', statuses[record.status] || record.status); state.dataset.status = record.status; badges.append(state);
    if (record.long_term) badges.append(el('span', 'registration-badge long-term', '长期使用登记'));
    if (record.has_conflict) badges.append(el('span', 'registration-badge conflict', '存在重叠登记 · 请协调'));
    heading.append(badges); card.append(heading);
    if (history) card.append(el('p', 'management-muted', record.machine_id));
    card.append(contact(record));
    card.append(el('p', 'management-muted', `服务器账号：${record.server_username || '待补充（请编辑登记）'}`));
    card.append(usagePanel(record.usage));
    const gpuText = record.gpu_indices?.length ? record.gpu_indices.map(index => `GPU ${index}`).join('、') : '未指定编号';
    card.append(el('p', '', `申请 GPU：${gpuText}，共 ${record.gpu_count} 张`), el('p', '', `预计使用：${formatDate(record.start_at)} — ${formatDate(record.end_at)}`));
    if (record.ended_at) card.append(el('p', 'management-muted', `实际结束登记：${formatDate(record.ended_at)}`));
    [description('使用方式', record.usage_pattern), description('研究用途', record.purpose, true), description('备注', record.notes, true)].filter(Boolean).forEach(node => card.append(node));
    const multiplePeriods = record.registration_periods?.length > 1;
    card.append(el('p', 'management-muted', `${multiplePeriods ? '最近连续登记' : '连续登记'} ${decimal(record.continuous_days)} 天 · 计划登记 ${decimal(record.planned_gpu_hours)} GPU 卡时 · 最近续期 ${formatDate(record.last_extended_at)}`));
    if (multiplePeriods) card.append(el('p', 'management-muted', '含到期后重新续期的多段计划，间隔时间不计入登记卡时。'));
    if (record.long_term && active(record)) card.append(el('p', 'registration-long-note', '该申请属于长期使用，请定期确认实际需求；如计划变更请及时调整。暂时空闲不会取消此登记。'));
    if (record.conflicts?.length) {
      const details = el('details', 'registration-notes'); details.append(el('summary', '', '查看重叠登记与联系方式'));
      record.conflicts.forEach(conflict => { const item = el('div', 'conflict-person'); item.append(el('p', '', `${conflict.name} · 预计至 ${formatDate(conflict.end_at)}`), contact(conflict)); details.append(item); });
      card.append(details);
    }
    const actions = el('div', 'management-actions');
    if (!['ended', 'cancelled'].includes(record.status)) {
      actions.append(button('编辑', () => openForm(record.machine_id, record, 'edit')), button('续期', () => openForm(record.machine_id, record, 'extend')));
      if (record.status === 'scheduled') actions.append(button('取消预约', () => changeState(record, 'cancel'), 'danger'));
      else actions.append(button('提前结束', () => changeState(record, 'end'), 'danger'));
    }
    actions.append(button('变更历史', () => openHistory(record))); card.append(actions); return card;
  }
  function ensureRegistrationRows() {
    if (!authenticated) return;
    let added = false;
    document.querySelectorAll('#machines-list > article[data-machine-id]').forEach(card => {
      const id = card.dataset.machineId;
      if (registrationRows.has(id)) return;
      const section = el('section', 'machine-registrations'); section.setAttribute('aria-label', `${id} GPU 使用登记`);
      const heading = el('div', 'management-section-heading'); heading.append(el('h3', '', `${id} · GPU 使用登记`));
      const controls = el('div', 'management-actions'), count = el('span', 'registration-count', '正在读取登记');
      controls.append(count, button('申请使用 GPU', () => openForm(id), 'primary')); heading.append(controls);
      const scope = el('p', 'management-muted registration-scope', '只能从现在开始登记；是否可申请只看已有有效登记，实时 GPU 占用仅供参考。');
      const list = el('div', 'registrations-list'); section.append(heading, scope, list); card.append(section);
      registrationRows.set(id, {section, list, count});
      added = true;
    });
    if (added) renderRegistrations();
  }
  function renderRegistrations() {
    if (!authenticated) return;
    registrationRows.forEach(({list, count}, id) => {
      const expanded = new Set([...list.querySelectorAll('.registration-card')].filter(card => card.querySelector('details[open]')).map(card => card.dataset.recordId));
      const current = sortRecords(records.filter(record => record.machine_id === id && active(record)));
      const people = new Set(current.map(record => record.wechat.normalize('NFKC').trim().toLowerCase())).size;
      const activeCount = current.filter(record => record.status === 'active').length, scheduledCount = current.length - activeCount;
      count.textContent = `有效登记 ${current.length} 条 · ${people} 人（使用中 ${activeCount} 条 / 待开始 ${scheduledCount} 条）`;
      list.replaceChildren(...(current.length ? current.map(record => recordCard(record)) : [el('p', 'management-muted', '暂无有效登记，可直接申请使用 GPU。')]));
      list.querySelectorAll('.registration-card').forEach(card => { if (expanded.has(card.dataset.recordId)) card.querySelectorAll('details').forEach(details => { details.open = true; }); });
    });
    renderHistory();
  }
  function renderHistory() {
    const visible = $('history-show').checked; $('history-controls').hidden = !visible; $('history-results').hidden = !visible;
    if (!visible || !authenticated) return;
    const machine = $('history-machine').value, query = $('history-search').value.trim().toLowerCase();
    const selected = sortRecords(records.filter(record => (!machine || record.machine_id === machine) && (!query || `${record.name} ${record.wechat}`.toLowerCase().includes(query))));
    $('history-results').replaceChildren(el('p', 'management-muted', `共 ${selected.length} 条记录`), ...selected.map(record => recordCard(record, true)));
  }
  async function refreshStatistics() {
    if (!authenticated) return;
    const generation = sessionGeneration, days = $('statistics-days').value;
    const data = await api(`/statistics?days=${encodeURIComponent(days)}`);
    if (!authenticated || generation !== sessionGeneration || days !== $('statistics-days').value) return;
    $('statistics-method').textContent = data.method_text || '统计窗口内已经发生的登记时长，不代表实际计算用量。同一微信号在同一服务器的重叠申请，按指定 GPU 并集与最大同时申请张数中的较大值去重；不同服务器分别累加。申请次数按本时段创建的登记统计，含后来取消的申请。';
    const people = data.people || [], table = el('table', 'registration-statistics-table');
    const head = el('thead'), tr = el('tr'); ['申请人', '申请次数', '登记 GPU 卡时', '当前有效申请', '长期登记时长'].forEach(title => { const th = el('th', '', title); th.scope = 'col'; tr.append(th); }); head.append(tr); table.append(head);
    const body = el('tbody'); people.forEach(person => {
      const row = el('tr'); const values = [`${person.name}（${person.wechat}）`, person.booking_count, decimal(person.gpu_hours), person.valid_reservations_count, `${decimal(person.long_term_hours)} 小时`];
      values.forEach((value, index) => { const td = el('td', '', String(value ?? '—')); td.dataset.label = tr.children[index].textContent; row.append(td); }); body.append(row);
    }); table.append(body);
    $('statistics-results').replaceChildren(el('p', 'management-muted', `${formatDate(data.from)} 至 ${formatDate(data.to)} · 合计 ${decimal(data.total_gpu_hours)} GPU 登记卡时`), people.length ? table : el('p', '', '本时间段暂无登记。'));
    people.forEach(person => {
      const item = el('article', 'person-usage'); item.append(el('h3', '', `${person.name} · ${person.wechat}`), usagePanel({...person.usage, account_status: 'bound'}));
      const own = records.filter(r => r.wechat.normalize('NFKC').trim().toLowerCase() === person.wechat.normalize('NFKC').trim().toLowerCase());
      item.append(el('p', 'management-muted', `关联服务器账号：${[...new Set(own.map(r => `${r.machine_id} / ${r.server_username || '待补账号'}`))].join('；') || '无'}。同一服务器账号由多人共用时，只能统计账号整体占用。`));
      $('statistics-results').append(item);
    });
  }
  function applySaved(data) {
    if (!data?.registration || !authenticated) return;
    registrationRevision++;
    records = [...records.filter(record => record.id !== data.registration.id), data.registration];
    renderRegistrations();
  }
  async function refreshAll(force = false) {
    if (!authenticated) return;
    if (loading) { if (force === true) refreshPending = true; return; }
    loading = true; const generation = sessionGeneration, revision = registrationRevision; $('refresh-registrations').disabled = true;
    try {
      const [list, summary] = await Promise.all([api('/registrations?history=1'), api('/overview')]);
      if (!authenticated || generation !== sessionGeneration) return;
      if (revision !== registrationRevision) { refreshPending = true; return; }
      records = list.registrations || []; overview = summary;
      $('registration-active-cards').textContent = String(summary.active_gpu_count ?? '—');
      $('registration-long-term').textContent = String(summary.long_term_count ?? '—');
      $('registration-expiring').textContent = String(summary.expiring_count ?? '—');
      ensureRegistrationRows(); renderRegistrations(); message($('registrations-message'), '登记无需审批，提交后立即公示。所有成员都可维护公共登记；操作不会终止计算任务。');
      await refreshStatistics();
    } catch (error) { if (authenticated) message($('registrations-message'), `登记更新失败：${error.message}`, true); }
    finally { if (generation === sessionGeneration) { loading = false; $('refresh-registrations').disabled = false; if (refreshPending && authenticated) { refreshPending = false; refreshAll(); } } }
  }
  function openDialog(title) {
    dialogGeneration++;
    $('registration-dialog-title').textContent = title; $('registration-dialog-content').replaceChildren();
    if (!$('registration-dialog').open) $('registration-dialog').showModal();
    return $('registration-dialog-content');
  }
  $('registration-dialog-close').addEventListener('click', () => $('registration-dialog').close());
  function field(form, labelText, name, type = 'text', value = '', options = {}) {
    const label = el('label', 'management-field', labelText), input = el(type === 'textarea' ? 'textarea' : 'input');
    if (type !== 'textarea') input.type = type;
    input.name = name; input.id = `registration-${name}`; input.value = value ?? '';
    Object.entries(options).forEach(([key, val]) => { input[key] = val; }); label.append(input); form.append(label); return input;
  }
  function showConflicts(target, conflicts, ackInput) {
    target.replaceChildren(); target.hidden = !conflicts.length;
    if (!conflicts.length) return;
    target.append(el('h3', '', '所选 GPU 已有登记，暂不可重复申请'));
    conflicts.forEach(record => {
      const card = el('div', 'conflict-person'); card.append(el('p', '', `${record.name} · ${record.gpu_count} 张 GPU · ${formatDate(record.start_at)} 至 ${formatDate(record.end_at)}`), contact(record));
      if (record.reason) card.append(el('p', 'management-muted', ({gpu_overlap: '指定 GPU 编号与已有登记重叠。', capacity_exceeded: '同一时间申请的 GPU 总数超过服务器可用卡数。'}[record.reason] || '同一时段存在可能影响资源安排的登记。'))); target.append(card);
    });
    target.append(el('p', 'management-muted', '请等待已有登记释放，或修改 GPU 编号和时间。不能确认冲突后继续提交。'));
  }
  function openForm(machineId, record = null, mode = 'create') {
    if (mode === 'extend' && !record?.server_username) mode = 'edit';
    const machine = inventory.find(item => item.id === machineId);
    if (!machine) { message($('registrations-message'), '服务器清单不可用，请重新载入页面。', true); return; }
    const root = openDialog(mode === 'extend' ? '延长登记时间' : mode === 'edit' ? '编辑公共登记' : '申请使用 GPU');
    const form = el('form', 'registration-form'); root.append(form);
    const formGeneration = dialogGeneration;
    const isCurrentForm = () => authenticated && formGeneration === dialogGeneration && $('registration-dialog').open && root.contains(form);
    form.append(el('p', 'management-muted', `${machine.id} · ${machine.gpu_model} × ${machine.gpu_count} · 所有时间均为北京时间（UTC+8）`));
    const grid = el('div', 'registration-form-grid'); form.append(grid);
    const name = field(grid, '申请人姓名 *', 'name', 'text', record?.name, {required: true, maxLength: 80, autoComplete: 'name'});
    const wechat = field(grid, '微信号 *', 'wechat', 'text', record?.wechat, {required: true, maxLength: 80});
    const username = field(grid, '该服务器账号 *（多个账号用英文空格分隔）', 'server_username', 'text', record?.server_username, {required: true, maxLength: 649, placeholder: '例如：zfgao wkli', autoCapitalize: 'none', spellcheck: false});
    username.pattern = ' *[A-Za-z_][A-Za-z0-9_.@\\-]{0,63}(?: +[A-Za-z_][A-Za-z0-9_.@\\-]{0,63}){0,9} *';
    username.title = '只填写 SSH 账号名，多个账号用英文空格分隔，例如 zfgao wkli。';
    const usernameHint = el('p', 'management-muted', '可填 1–10 个 SSH 账号，每个不超过 64 个字符，只用英文空格分隔。请勿填密码、逗号或中文。所有账号合并统计，同卡同时使用不重复计时。');
    usernameHint.id = 'server-username-hint'; username.setAttribute('aria-describedby', usernameHint.id); username.after(usernameHint);
    const countLabel = el('label', 'management-field', 'GPU 申请数量 *'), count = el('select'); count.name = 'gpu_count'; count.required = true;
    for (let index = 1; index <= machine.gpu_count; index++) { const option = el('option', '', String(index)); option.value = String(index); count.append(option); }
    count.value = String(record?.gpu_count || 1); countLabel.append(count); grid.append(countLabel);
    const start = field(grid, '计划开始时间 *（北京时间）', 'start_at', 'datetime-local', beijingValue(record?.start_at || new Date()), {required: true});
    start.readOnly = true;
    grid.append(el('p', 'management-muted', '不允许提前预约。新登记在提交时立即开始，开始时间由服务器确认。'));
    const durationLabel = el('label', 'management-field', mode === 'extend' ? '续期时长（从当前时间起）' : '预计使用时长'), duration = el('select');
    [[8, '8 小时'], [24, '24 小时'], [48, '48 小时'], [72, '72 小时'], [168, '7 天'], ['custom', '自定义结束时间']].forEach(([value, text]) => { const option = el('option', '', text); option.value = String(value); duration.append(option); });
    duration.value = record ? 'custom' : '24'; durationLabel.append(duration); grid.append(durationLabel);
    grid.append(el('p', 'management-muted', '每次登记最多 7 天。续期后的结束时间最多为当前时间起 7 天。'));
    const end = field(grid, '预计结束时间 *（北京时间）', 'end_at', 'datetime-local', beijingValue(record?.end_at || Date.now() + 24 * 3600000), {required: true});
    const indices = el('fieldset', 'gpu-choice'); indices.append(el('legend', '', '指定 GPU 编号（选填；不选则仅登记数量）'));
    const indexInputs = []; for (let index = 0; index < machine.gpu_count; index++) { const label = el('label'), checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.value = String(index); checkbox.checked = Boolean(record?.gpu_indices?.includes(index)); label.append(checkbox, el('span', '', `GPU ${index}`)); indices.append(label); indexInputs.push(checkbox); }
    form.append(indices);
    const pattern = field(form, '使用方式（可选择或自行填写）', 'usage_pattern', 'text', record?.usage_pattern, {maxLength: 300}); pattern.setAttribute('list', 'registration-patterns');
    const patterns = el('datalist'); patterns.id = 'registration-patterns'; ['连续训练，预计全天占用', '每天夜间使用', '每周集中计算 3 天', '阶段性训练，具体时间请联系协调'].forEach(value => { const option = el('option'); option.value = value; patterns.append(option); }); form.append(patterns);
    const purpose = field(form, '计算任务或研究用途（长期登记建议填写）', 'purpose', 'text', record?.purpose, {maxLength: 500});
    const notes = field(form, '详细备注（最多 4000 字）', 'notes', 'textarea', record?.notes, {maxLength: 4000, rows: 4});
    const longNotice = el('p', 'registration-long-notice', '您正在申请较长时间的 GPU 使用。请明确预计使用周期和实际需求，避免影响其他同学。长期计划发生变化时，请及时修改或结束登记。长期登记无需审批，也不会因 GPU 暂时空闲而消失。'); form.append(longNotice);
    let confirmChange = null;
    if (record) {
      const confirmation = el('label', 'management-inline-check registration-change-confirmation');
      confirmChange = el('input'); confirmChange.type = 'checkbox'; confirmChange.name = 'confirm_shared_change'; confirmChange.required = true;
      confirmation.append(confirmChange, el('span', '', `我确认${mode === 'extend' ? '延长' : '修改'} ${record.name} 在 ${record.machine_id} 的公共登记，以上表单是本次拟保存的使用计划；该操作会记录变更历史。`));
      form.append(confirmation);
    }
    const conflictBox = el('section', 'registration-conflicts'); conflictBox.hidden = true; const ack = el('input'); ack.type = 'checkbox'; form.append(conflictBox);
    const feedback = el('p', 'management-message'); feedback.setAttribute('role', 'status'); feedback.setAttribute('aria-live', 'polite'); form.append(feedback);
    const footer = el('div', 'management-actions dialog-footer'), submit = el('button', 'management-button primary', mode === 'create' ? '提交登记' : mode === 'extend' ? '确认续期' : '保存修改'); submit.type = 'submit';
    footer.append(submit, button('返回', () => $('registration-dialog').close())); form.append(footer);
    let previewSignature = '', mutationKey = '', mutationSignature = '', submitting = false;
    if (mode === 'extend') { [name, wechat, username, count, start, pattern, purpose, notes, ...indexInputs].forEach(input => { input.disabled = true; }); }
    const showLong = () => { try { longNotice.hidden = !isLongTerm(beijingISO(start.value), beijingISO(end.value)); } catch { longNotice.hidden = true; } };
    const adjustEnd = () => {
      if (duration.value !== 'custom' && start.value) {
        try { const base = mode === 'extend' ? Math.max(Date.parse(record.start_at), Date.now()) : Date.parse(beijingISO(start.value)); end.value = beijingValue(base + Number(duration.value) * 3600000); } catch { /* Native form validation reports missing times. */ }
      }
      showLong();
    };
    duration.addEventListener('change', adjustEnd); start.addEventListener('change', adjustEnd); end.addEventListener('input', () => { duration.value = 'custom'; showLong(); });
    indexInputs.forEach(input => input.addEventListener('change', () => { const selected = indexInputs.filter(item => item.checked).length; if (selected) count.value = String(selected); }));
    const invalidateConfirmation = event => {
      if (event.target === ack || event.target === confirmChange) return;
      previewSignature = ''; ack.checked = false; conflictBox.hidden = true;
      if (confirmChange) confirmChange.checked = false;
    };
    form.addEventListener('input', invalidateConfirmation);
    form.addEventListener('change', invalidateConfirmation);
    showLong();
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (submitting || !isCurrentForm()) return;
      let payload;
      try {
        const selected = indexInputs.filter(input => input.checked).map(input => Number(input.value)), gpuCount = Number(count.value);
        if (selected.length && selected.length !== gpuCount) throw new Error('指定 GPU 编号数量必须与申请数量一致。');
        const startAt = formISO(start.value, record?.start_at), endAt = formISO(end.value, record?.end_at);
        if (Date.parse(endAt) <= Date.parse(startAt)) throw new Error('结束时间必须晚于开始时间。');
        const unchangedTime = record && startAt === record.start_at && endAt === record.end_at;
        const durationAnchor = record ? Math.max(Date.parse(startAt), Date.now()) : Date.parse(startAt);
        if (!unchangedTime && Date.parse(endAt) - durationAnchor > 7 * 86400000) throw new Error('每次登记最多 7 天，请缩短使用时间。');
        payload = {machine_id: machineId, server_username: normalizeUsernames(username.value), name: name.value.trim(), wechat: wechat.value.trim(), gpu_count: gpuCount, gpu_indices: selected, start_at: startAt, end_at: endAt, purpose: purpose.value.trim(), notes: notes.value.trim(), usage_pattern: pattern.value.trim()};
        if (!payload.name || !payload.wechat) throw new Error('请填写姓名和微信号。');
        if (mode === 'extend' && Date.parse(endAt) <= Math.max(Date.parse(record.end_at), Date.now())) throw new Error('续期结束时间必须晚于原结束时间和当前时间。');
        if (confirmChange && !confirmChange.checked) throw new Error('请先勾选确认，说明您已核对这条公共登记的修改内容。');
      } catch (error) { message(feedback, error.message, true); return; }
      const signature = JSON.stringify(payload);
      submitting = true; submit.disabled = true;
      // The submitted snapshot stays identical to the visible form while preview/save is pending.
      const disabledStates = [...form.querySelectorAll('input, select, textarea, button')].map(input => [input, input.disabled]);
      disabledStates.forEach(([input]) => { input.disabled = true; });
      try {
        if (previewSignature !== signature) {
          const preview = await api('/registrations/preview', {method: 'POST', body: {...payload, ...(record ? {exclude_id: record.id} : {})}});
          if (!isCurrentForm()) return;
          previewSignature = signature; ack.checked = false; showConflicts(conflictBox, preview.blocked === false ? [] : (preview.conflicts || []), ack);
          if (preview.blocked === true || (preview.blocked === undefined && preview.conflicts?.length)) { message(feedback, '已有有效登记，不能重复申请。请修改 GPU 编号或时间。', true); conflictBox.scrollIntoView({block: 'nearest'}); return; }
        }
        if (!conflictBox.hidden) { message(feedback, '已有有效登记，不能重复申请。', true); return; }
        const body = mode === 'extend' ? {end_at: payload.end_at, expected_version: record.version, conflict_ack: ack.checked} : {...payload, ...(record ? {expected_version: record.version} : {}), conflict_ack: ack.checked};
        const path = mode === 'create' ? '/registrations' : `/registrations/${encodeURIComponent(record.id)}${mode === 'extend' ? '/extend' : ''}`;
        const nextSignature = JSON.stringify({path, body}); if (mutationSignature !== nextSignature) { mutationSignature = nextSignature; mutationKey = uid(); }
        const saved = await api(path, {method: mode === 'edit' ? 'PATCH' : 'POST', body, key: mutationKey});
        applySaved(saved);
        // A completed request may belong to a dialog the user has already closed or replaced.
        if (isCurrentForm()) { $('registration-dialog').close(); root.replaceChildren(); }
        await refreshAll(true);
        if (authenticated) message($('registrations-message'), '登记已保存并公示。使用计划变更后请及时更新。');
      } catch (error) {
        if (!authenticated) return;
        if (!isCurrentForm()) { message($('registrations-message'), `先前表单的操作未完成：${error.message}`, true); return; }
        if (error.status === 409 && error.details?.conflicts?.length) { previewSignature = signature; ack.checked = false; mutationSignature = ''; showConflicts(conflictBox, error.details.conflicts, ack); message(feedback, '刚刚有其他人完成登记，请重新选择可用 GPU。', true); }
        else if (error.status === 409 && error.code === 'version_conflict') { message(feedback, `${error.message} 请关闭表单并刷新登记后重新编辑，避免覆盖他人的修改。`, true); await refreshAll(); }
        else message(feedback, error.message, true);
      } finally {
        disabledStates.forEach(([input, disabled]) => { input.disabled = disabled; });
        submitting = false; submit.disabled = false;
      }
    });
    (mode === 'extend' ? end : name).focus();
  }
  function changeState(record, action) {
    const actionLabel = action === 'cancel' ? '取消尚未开始的预约' : '提前结束登记';
    const root = openDialog(`确认${actionLabel}`), generation = dialogGeneration, key = uid();
    const isCurrent = () => authenticated && generation === dialogGeneration && $('registration-dialog').open;
    root.append(el('p', '', `申请人：${record.name}`), el('p', '', `服务器：${record.machine_id} · ${record.gpu_count} 张 GPU`), el('p', '', `预计使用：${formatDate(record.start_at)} — ${formatDate(record.end_at)}`));
    root.append(el('p', 'registration-long-notice', `确认后将${action === 'cancel' ? '取消这条未来预约' : '结束这条有效登记'}，不再计入有效申请。所有成员均可维护公共登记，操作会保留变更历史，不会中止服务器上的计算任务。`));
    const feedback = el('p', 'management-message'); feedback.setAttribute('role', 'status'); root.append(feedback);
    const actions = el('div', 'management-actions dialog-footer');
    const submit = button(`确认${action === 'cancel' ? '取消预约' : '结束登记'}`, async () => {
      if (!isCurrent() || submit.disabled) return;
      submit.disabled = true; message(feedback, '正在保存…');
      try {
        const saved = await api(`/registrations/${encodeURIComponent(record.id)}/${action}`, {method: 'POST', body: {expected_version: record.version}, key});
        applySaved(saved);
        if (isCurrent()) { $('registration-dialog').close(); root.replaceChildren(); }
        await refreshAll(true);
      } catch (error) {
        if (!authenticated) return;
        message(isCurrent() ? feedback : $('registrations-message'), error.message + (error.status === 409 ? ' 请刷新后重新操作。' : ''), true);
      } finally { submit.disabled = false; }
    }, 'primary');
    actions.append(submit, button('返回，不做修改', () => $('registration-dialog').close())); root.append(actions);
  }
  async function openHistory(record) {
    const root = openDialog(`${record.name} · 变更历史`); root.append(el('p', 'management-muted', '正在读取…'));
    const requestedDialog = dialogGeneration;
    try {
      const data = await api(`/registrations/${encodeURIComponent(record.id)}/history`); if (!authenticated || requestedDialog !== dialogGeneration) return;
      root.replaceChildren(el('p', 'management-muted', `${record.machine_id} · 所有时间均为北京时间`));
      const labels = {account_verified: '采样确认服务器账号', admin_create: '管理员登记', create: '创建登记', edit: '编辑登记', extend: '延长时间', end: '提前结束', cancel: '取消预约'};
      const fields = {name: '申请人', wechat: '微信号', server_username: '服务器账号', gpu_count: 'GPU 数量', gpu_indices: 'GPU 编号', start_at: '开始时间', end_at: '结束时间', purpose: '研究用途', notes: '备注', usage_pattern: '使用方式'};
      (data.events || []).forEach(event => {
        const article = el('article', 'registration-card'); article.append(el('h3', '', labels[event.action] || event.action || '登记变更'), el('p', 'management-muted', formatDate(event.created_at || event.at || event.timestamp)));
        const changes = event.after || event.snapshot || event.data || {}; const before = event.before || {};
        Object.entries(fields).forEach(([key, label]) => { if (changes[key] !== undefined && JSON.stringify(changes[key]) !== JSON.stringify(before[key])) article.append(el('p', 'registration-description', `${label}：${key.endsWith('_at') ? formatDate(changes[key]) : Array.isArray(changes[key]) ? changes[key].join('、') : String(changes[key])}`)); });
        root.append(article);
      });
      if (!data.events?.length) root.append(el('p', '', '暂无变更历史。'));
    } catch (error) { if (authenticated && requestedDialog === dialogGeneration) root.replaceChildren(el('p', 'management-message', error.message)); }
  }
  inventory.forEach(machine => { const option = el('option', '', machine.id); option.value = machine.id; $('history-machine').append(option); });
  $('history-show').addEventListener('change', renderHistory); $('history-machine').addEventListener('change', renderHistory); $('history-search').addEventListener('input', renderHistory);
  $('statistics-days').addEventListener('change', () => refreshStatistics().catch(error => { if (authenticated) message($('registrations-message'), error.message, true); }));
  $('refresh-registrations').addEventListener('click', refreshAll);
  setInterval(() => { if (authenticated && !document.hidden && !$('registration-dialog').open) refreshAll(); }, 30000);
  let initial = null; try { initial = JSON.parse($('machines-initial-session')?.textContent || 'null'); } catch { /* Verify the server session if the embedded state is missing. */ }
  if (initial?.authenticated) enter(initial);
  else if (initial?.authenticated === false) lock();
  else api('/session').then(enter).catch(() => lock());
})();
