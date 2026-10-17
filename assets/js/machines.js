(() => {
  "use strict";
  const config = window.MACHINES_CONFIG || {};
  const $ = id => document.getElementById(id);
  const number = value => typeof value === "number" && Number.isFinite(value);
  const gib = value => number(value) ? (value / 1024).toFixed(1) : "—";
  const capacity = value => !number(value) ? "—" : value >= 1048576 ? `${(value / 1048576).toFixed(1)} TiB` : `${(value / 1024).toFixed(1)} GiB`;
  const sampleFormat = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour12: false,
    hour: "2-digit", minute: "2-digit", second: "2-digit"
  });
  let inventory = [], snapshots = new Map(), fetchedAt = 0, haveResponse = false, failed = false, busy = false;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function state(machine) {
    if (machine?.status === "online" && number(machine.age_seconds)) {
      return machine.age_seconds + (performance.now() - fetchedAt) / 1000 <= (config.staleAfterSeconds || 120) ? "online" : "stale";
    }
    return machine?.status || "waiting";
  }
  function renderSummary() {
    if (!inventory.length) return;
    const counts = { total: 0, free: 0, used: 0, unknown: 0 };
    inventory.forEach(item => {
      const total = item.gpu_count;
      counts.total += total;
      const machine = snapshots.get(item.id);
      if (state(machine) !== "online") { counts.unknown += total; return; }
      // Missing, duplicated, or unreadable card indices cannot establish availability.
      const cards = new Map();
      (machine.telemetry?.gpus || []).forEach(gpu => {
        if (!Number.isInteger(gpu.index) || gpu.index < 0 || gpu.index >= total) return;
        cards.set(gpu.index, cards.has(gpu.index) ? null : gpu);
      });
      let readable = 0;
      cards.forEach(gpu => {
        if (!gpu || !number(gpu.utilization_pct) || gpu.utilization_pct < 0 || gpu.utilization_pct > 100
            || !number(gpu.memory_used_mib) || gpu.memory_used_mib < 0
            || !number(gpu.memory_total_mib) || gpu.memory_total_mib <= 0 || gpu.memory_used_mib > gpu.memory_total_mib) return;
        readable++;
        if (gpu.utilization_pct < 10 && gpu.memory_used_mib < 1024) counts.free++;
        else counts.used++;
      });
      counts.unknown += total - readable;
    });
    const ready = haveResponse || failed;
    ["total", "free", "used", "unknown"].forEach(key => {
      $(`summary-${key}`).textContent = key === "total" || ready ? String(counts[key]) : "—";
    });
    $("summary-servers").textContent = `${inventory.length} 台服务器`;
    $("summary-unknown").closest(".summary-card").dataset.warning = String(ready && counts.unknown > 0);
  }
  function gpuTable(gpus, current) {
    const table = element("table", "gpu-table");
    table.setAttribute("aria-label", "各张 GPU 的占用和显存");
    const thead = element("thead"), titles = element("tr");
    ["GPU", "占用率", "显存（已用 / 总量）"].forEach(text => {
      const th = element("th", "", text); th.scope = "col"; titles.append(th);
    });
    thead.append(titles);
    const tbody = element("tbody");
    gpus.forEach(gpu => {
      const row = element("tr");
      const label = number(gpu.index) ? `GPU ${gpu.index}` : "编号未知";
      row.append(element("td", "", label));
      const usage = element("td"), load = element("div", "gpu-load");
      const utilization = current === "online" && number(gpu.utilization_pct) ? gpu.utilization_pct : null;
      load.append(element("span", number(utilization) ? "" : "gpu-unavailable", number(utilization) ? `${Math.round(utilization)}%` : "—"));
      if (number(utilization)) {
        const track = element("span", "gpu-track"), fill = element("span");
        track.setAttribute("aria-hidden", "true");
        fill.style.width = `${Math.max(0, Math.min(100, utilization))}%`;
        fill.dataset.high = String(utilization >= 80); track.append(fill); load.append(track);
      }
      usage.append(load); row.append(usage);
      const used = current === "online" ? gpu.memory_used_mib : null;
      const memory = element("td", number(used) ? "" : "gpu-unavailable");
      if (!number(gpu.index) && !number(gpu.memory_total_mib)) {
        memory.append(element("span", "gpu-problem", current === "online" ? "读取异常" : "上次读取异常"));
      } else {
        memory.textContent = `${gib(used)} / ${gib(gpu.memory_total_mib)} GiB`;
      }
      row.append(memory); tbody.append(row);
    });
    table.append(thead, tbody); return table;
  }
  function resourceLine(label, value, ratio) {
    const line = element("div", "machine-resource");
    const values = element("div", "resource-values");
    values.append(element("span", "resource-label", label), element("span", "", value));
    line.append(values);
    if (number(ratio)) {
      const track = element("div", "resource-track"), fill = element("span");
      track.setAttribute("aria-hidden", "true");
      fill.style.width = `${Math.min(100, Math.max(0, ratio))}%`;
      fill.dataset.high = String(ratio >= 90); track.append(fill); line.append(track);
    }
    return line;
  }
  function resources(data, current) {
    const live = current === "online";
    const section = element("div", "machine-resources");
    const cpu = live && number(data?.cpu_percent) ? data.cpu_percent : null;
    section.append(resourceLine("CPU", number(cpu) ? `${cpu.toFixed(1)}%` : "—", cpu));
    const used = live ? data?.memory_used_mib : null, total = data?.memory_total_mib;
    const ratio = number(used) && number(total) && total > 0 ? used / total * 100 : null;
    section.append(resourceLine("内存", `${gib(used)} / ${gib(total)} GiB`, ratio));
    const diskSection = element("div", "machine-disks");
    diskSection.append(element("span", "resource-label", "硬盘 · 可用 / 总量"));
    if (data?.disks?.length) {
      data.disks.forEach(disk => {
        const free = live ? disk.available_mib : null;
        const line = element("div", "machine-disk");
        const low = number(free) && number(disk.total_mib) && disk.total_mib > 0 && free / disk.total_mib < 0.1;
        line.dataset.low = String(low);
        const label = disk.mountpoint === "/" ? "系统盘 /" : disk.mountpoint;
        line.append(element("span", "disk-mount", label), element("span", "disk-capacity", `${capacity(free)} / ${capacity(disk.total_mib)}`));
        if (low) line.append(element("span", "disk-warning", "空间不足 10%"));
        diskSection.append(line);
      });
      if (data.disk_error) diskSection.append(element("span", "resource-unknown", data.disk_error === "query_failed" ? "硬盘信息暂不可读" : "部分硬盘信息暂不可读"));
    } else {
      diskSection.append(element("span", "resource-unknown", "暂未获取"));
    }
    section.append(diskSection); return section;
  }
  function cpuConfiguration(data) {
    const section = element("div", "machine-cpu-config");
    section.append(element("span", "resource-label", "CPU 配置"));
    const model = data?.cpu_model?.replace(/\(R\)|\(TM\)/g, "").replace(/\s+CPU\s+@\s+/, " · ");
    section.append(element("span", "cpu-model", model || "暂未获取"));
    const cores = number(data?.cpu_physical_cores) ? `${data.cpu_physical_cores} 核` : null;
    const threads = number(data?.cpu_logical_cpus) ? `${data.cpu_logical_cpus} 线程` : null;
    const sockets = number(data?.cpu_sockets) && data.cpu_sockets > 1 ? `${data.cpu_sockets} 颗 CPU` : null;
    const counts = [sockets, cores, threads].filter(Boolean);
    if (counts.length) section.append(element("span", "cpu-counts", counts.join(" · ")));
    return section;
  }
  function machineRow(item) {
    const machine = snapshots.get(item.id), current = state(machine);
    const row = element("tr"), ip = element("th"); ip.scope = "row";
    ip.append(element("span", "machine-ip", item.id));
    const badge = element("span", "machine-badge", current === "online" ? "占用已更新" : "占用未更新");
    badge.dataset.state = current; ip.append(badge);
    const sampledAt = machine?.telemetry?.observed_at;
    if (sampledAt && Number.isFinite(Date.parse(sampledAt))) {
      ip.append(element("span", "machine-last", `最近采样 ${sampleFormat.format(new Date(sampledAt))}`));
    }
    ip.append(resources(machine?.telemetry, current));
    const hardware = element("td");
    hardware.append(element("span", "machine-model", item.gpu_model), element("span", "machine-count", `${item.gpu_count} 张`));
    hardware.append(cpuConfiguration(machine?.telemetry));
    const usage = element("td");
    if (machine?.telemetry?.gpus?.length) {
      usage.append(gpuTable(machine.telemetry.gpus, current));
    } else {
      usage.append(element("p", "machine-empty", "占用数据暂不可用"));
    }
    row.append(ip, hardware, usage); return row;
  }
  function render() {
    renderSummary();
    if (inventory.length) $("machines-list").replaceChildren(...inventory.map(machineRow));
    const message = $("machines-message");
    const online = inventory.filter(item => state(snapshots.get(item.id)) === "online").length;
    message.dataset.warning = String(Boolean(config.reportingPending || failed || (haveResponse && online < inventory.length)));
    message.textContent = config.reportingPending ? "实时自动更新尚未接通。显卡配置已核实；占用数据未更新时显示“—”，不代表空闲。"
      : failed ? "暂时无法获取最新占用，正在重试。显卡配置仍可查看。"
      : !haveResponse ? "正在获取服务器资源数据…"
      : online < inventory.length ? `${online} / ${inventory.length} 台服务器的占用已更新，其余暂不可用。每 30 秒刷新。`
      : "每 30 秒刷新 GPU、CPU、内存和硬盘使用情况。";
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
      snapshots = new Map(payload.machines.map(machine => [machine.id, machine]));
      fetchedAt = performance.now(); haveResponse = true; failed = false;
    } catch { failed = true; }
    finally { clearTimeout(timeout); busy = false; $("refresh-machines").disabled = false; render(); }
  }
  $("refresh-machines").addEventListener("click", refresh);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  fetch("/assets/machines/inventory.json?v=ip2", { cache: "no-store", credentials: "omit" })
    .then(response => { if (!response.ok) throw new Error("Inventory unavailable"); return response.json(); })
    .then(data => { inventory = data; render(); })
    .catch(() => { $("machines-list").replaceChildren(); $("machines-message").textContent = "服务器列表读取失败，请刷新页面。"; });
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, config.refreshMs || 30000);
  setInterval(() => { if (!document.hidden && haveResponse) render(); }, 5000);
})();
