(() => {
  "use strict";
  const config = window.MACHINES_CONFIG || {};
  const $ = id => document.getElementById(id);
  const number = value => typeof value === "number" && Number.isFinite(value);
  const percent = value => number(value) ? `${Math.round(value)}%` : "—";
  const gib = value => number(value) ? (value / 1024).toFixed(1) : "—";
  const timeFormat = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const dateFormat = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" });
  let machines = [], fetchedAt = 0, lastSuccess = null, haveResponse = false, failed = false, busy = false;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function state(machine) {
    if (machine.status === "online" && number(machine.age_seconds)) {
      return machine.age_seconds + (performance.now() - fetchedAt) / 1000 <= (config.staleAfterSeconds || 120) ? "online" : "stale";
    }
    return machine.status || "unknown";
  }
  function quiet(gpu) {
    return number(gpu.utilization_pct) && number(gpu.memory_used_mib) && number(gpu.memory_total_mib) &&
      gpu.memory_total_mib > 0 && gpu.utilization_pct <= 10 && gpu.memory_used_mib / gpu.memory_total_mib <= 0.2;
  }
  function metric(label, value, detail, ratio) {
    const row = element("div", "gpu-metric");
    row.append(element("span", "", label));
    const body = element("div");
    const values = element("div", "gpu-value");
    values.append(element("span", "", value), element("span", "", detail));
    const track = element("div", "gpu-track");
    track.setAttribute("aria-hidden", "true");
    if (number(ratio)) {
      const fill = element("span");
      fill.style.width = `${Math.min(100, Math.max(0, ratio))}%`;
      fill.dataset.high = String(ratio >= 80);
      track.append(fill);
    }
    body.append(values, track); row.append(body); return row;
  }
  function gpuRow(gpu) {
    const row = element("div", "gpu-row");
    const title = element("div", "gpu-title");
    const name = element("span", "gpu-name");
    name.append(element("span", "gpu-index", number(gpu.index) ? `#${gpu.index}` : gpu.pci_bus_id || "未知编号"), document.createTextNode(gpu.name));
    const known = number(gpu.utilization_pct) && number(gpu.memory_used_mib) && number(gpu.memory_total_mib) && gpu.memory_total_mib > 0;
    const status = element("span", "gpu-state", !known ? "部分指标不可读" : quiet(gpu) ? "低占用" : "使用中");
    status.dataset.quiet = String(known && quiet(gpu)); title.append(name, status); row.append(title);
    row.append(metric("利用率", percent(gpu.utilization_pct), "GPU", gpu.utilization_pct));
    const ratio = number(gpu.memory_used_mib) && number(gpu.memory_total_mib) && gpu.memory_total_mib > 0 ? gpu.memory_used_mib / gpu.memory_total_mib * 100 : null;
    const free = number(ratio) ? `${gib(Math.max(0, gpu.memory_total_mib - gpu.memory_used_mib))} GiB 剩余` : "剩余量未知";
    row.append(metric("显存", `${gib(gpu.memory_used_mib)} / ${gib(gpu.memory_total_mib)} GiB`, free, ratio));
    row.append(element("p", "gpu-environment", `温度 ${number(gpu.temperature_c) ? Math.round(gpu.temperature_c) + " °C" : "—"}　·　功耗 ${number(gpu.power_w) ? Math.round(gpu.power_w) + " W" : "—"}`));
    return row;
  }
  function card(machine) {
    const current = state(machine);
    const data = current === "online" ? machine.telemetry : null;
    const article = element("article", "machine-card");
    const head = element("div", "machine-card__head");
    const title = element("div");
    title.append(element("h2", "", machine.name), element("p", "machine-address", `${machine.id} · ${machine.location}${machine.position ? " · " + machine.position : ""}`));
    const badge = element("span", "machine-badge", ({ online: "在线", stale: "数据中断", waiting: "未接入", unknown: "状态未知" })[current] || "状态未知");
    badge.dataset.state = current; head.append(title, badge); article.append(head);
    const cpu = element("div", "machine-cpu");
    cpu.append(element("span", "", `CPU ${percent(data?.cpu_percent)}`), element("span", "", `内存 ${gib(data?.memory_used_mib)} / ${gib(data?.memory_total_mib)} GiB`));
    article.append(cpu);
    if (data && data.gpus.length) {
      const gpus = element("div", "machine-gpus");
      data.gpus.forEach(gpu => gpus.append(gpuRow(gpu))); article.append(gpus);
    } else {
      const message = current === "stale" ? "暂未收到新的状态，最近一次指标已过期。" : current === "waiting" ? "等待服务器首次上报。" : current !== "online" ? "暂时无法确认这台机器的状态。" : data.gpu_error ? "GPU 指标暂不可读。" : "未检测到 NVIDIA GPU。";
      article.append(element("p", "machine-empty", message));
    }
    const last = machine.received_at ? `最近上报：${dateFormat.format(new Date(machine.received_at))} ${timeFormat.format(new Date(machine.received_at))}` : "最近上报：—";
    article.append(element("p", "machine-last", last)); return article;
  }
  function render() {
    const online = machines.filter(machine => state(machine) === "online");
    const gpus = online.flatMap(machine => machine.telemetry?.gpus || []);
    $("online-count").textContent = haveResponse ? online.length : "—";
    $("machine-total").textContent = machines.length ? `共 ${machines.length} 台机器` : "正在读取";
    $("gpu-count").textContent = haveResponse ? gpus.length : "—";
    $("quiet-count").textContent = haveResponse ? gpus.filter(quiet).length : "—";
    if (lastSuccess) {
      $("updated-time").textContent = timeFormat.format(lastSuccess);
      $("updated-date").textContent = `${dateFormat.format(lastSuccess)} · 北京时间`;
    }
    const query = $("machine-search").value.trim().toLowerCase();
    const location = $("location-filter").value, filter = $("status-filter").value;
    const visible = machines.filter(machine => {
      if (location && machine.location !== location) return false;
      if (filter === "quiet" && !(state(machine) === "online" && machine.telemetry?.gpus.some(quiet))) return false;
      if (filter === "interrupted" && state(machine) === "online") return false;
      const text = [machine.id, machine.name, machine.location, ...(machine.telemetry?.gpus || []).map(gpu => gpu.name)].join(" ").toLowerCase();
      return !query || text.includes(query);
    });
    $("machines-grid").replaceChildren(...visible.map(card));
    if (!visible.length && machines.length) $("machines-grid").append(element("p", "machines-empty", "没有符合条件的机器。"));
    const message = $("machines-message"); message.dataset.error = String(failed);
    document.querySelector(".live-dot").dataset.error = String(failed);
    message.textContent = failed ? "暂时无法获取最新状态，正在自动重试。请留意各台机器的最近上报时间。" : !haveResponse ? "正在获取服务器状态…" : `已收到 ${online.length} / ${machines.length} 台机器的最新状态；${machines.filter(machine => state(machine) === "waiting").length} 台尚未接入。`;
  }
  async function refresh() {
    if (busy) return;
    busy = true; $("refresh-machines").disabled = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(config.statusUrl, { cache: "no-store", credentials: "omit", signal: controller.signal });
      if (!response.ok) throw new Error("Status unavailable");
      const payload = await response.json();
      if (payload.schema_version !== 1 || !Array.isArray(payload.machines) || !payload.machines.length || payload.machines.some(machine => !machine.id || !["online", "stale", "waiting"].includes(machine.status) || (machine.telemetry && !Array.isArray(machine.telemetry.gpus)))) throw new Error("Invalid status");
      machines = payload.machines; fetchedAt = performance.now(); lastSuccess = new Date(); haveResponse = true; failed = false;
    } catch { failed = true; }
    finally { clearTimeout(timeout); busy = false; $("refresh-machines").disabled = false; render(); }
  }
  ["location-filter", "status-filter", "machine-search"].forEach(id => $(id).addEventListener(id === "machine-search" ? "input" : "change", render));
  $("refresh-machines").addEventListener("click", refresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  fetch("/assets/machines/inventory.json", { credentials: "omit" }).then(response => response.json()).then(data => { if (!haveResponse) { machines = data; render(); } }).catch(() => {});
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, config.refreshMs || 30000);
  setInterval(() => { if (!document.hidden && haveResponse) render(); }, 5000);
})();
