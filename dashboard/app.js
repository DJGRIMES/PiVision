const API_BASE = `${window.location.origin}/api/v1`;
const REFRESH_INTERVAL_MS = 5000;

function createEmptySeries() {
  return Array.from({ length: 12 }, () => 0);
}

function createEmptyDashboardData() {
  return {
    system: {
      cpu: null,
      memory: null,
      diskRemainingGb: null,
      tempC: null,
      uptime: "—",
    },
    ingest: {
      success60m: 0,
      failure60m: 0,
      avgLatencyMs: 0,
      series: createEmptySeries(),
    },
    queue: {
      depth: 0,
      running: 0,
      failed: 0,
      dead: 0,
      maxVisual: 40,
    },
    database: {
      connected: true,
      version: "SQLite",
      captures: 0,
      events: 0,
      jobs: 0,
      devices: 0,
      ingestAudit: 0,
      dbSizeMb: 0,
      tables: [],
    },
    devices: [],
    events: [],
    alerts: [],
  };
}

let dashboardData = createEmptyDashboardData();
let refreshTimer = null;

const el = (selector) => document.querySelector(selector);

function formatValue(value, unit = "") {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  return `${value}${unit}`;
}

function renderStat(containerId, entries) {
  const container = el(containerId);
  container.innerHTML = entries
    .map(
      ([label, value]) => `
      <div class="stat">
        <div class="label">${label}</div>
        <div class="value">${value}</div>
      </div>`
    )
    .join("");
}

function renderChart(series) {
  const chart = el("#ingest-chart");
  const safeSeries = series.length ? series : createEmptySeries();
  const max = Math.max(...safeSeries, 1);
  chart.innerHTML = safeSeries
    .map((value) => `<div class="bar" style="height:${Math.max(10, Math.round((value / max) * 100))}%"></div>`)
    .join("");
}

function renderDevices(devices) {
  const table = el("#device-table");
  if (!devices.length) {
    table.innerHTML = `
      <tr>
        <td colspan="5" class="empty-state">No devices have checked in yet.</td>
      </tr>`;
    return;
  }

  table.innerHTML = devices
    .map(
      (device) => `
      <tr>
        <td>${device.device_id}</td>
        <td>${formatLocalTime(device.last_seen)}</td>
        <td>${device.rssi ?? "—"}</td>
        <td>${device.battery_mv ? (device.battery_mv / 1000).toFixed(2) + " V" : "—"}</td>
        <td>${device.fw_version ?? "—"}</td>
      </tr>`
    )
    .join("");
}

function renderQueueMeter(queue) {
  const pct = Math.min(100, Math.round((queue.depth / queue.maxVisual) * 100));
  el("#queue-bar").style.width = `${pct}%`;
}

function renderDatabase(database) {
  renderStat("#db-health", [
    ["Connected", database.connected ? "Yes" : "No"],
    ["Version", database.version],
    ["Capture rows", database.captures],
    ["Event rows", database.events],
  ]);

  renderStat("#db-storage", [
    ["Jobs", database.jobs],
    ["Devices", database.devices],
    ["Ingest audit", database.ingestAudit],
    ["DB size", `${database.dbSizeMb.toFixed(1)} MB`],
  ]);

  const tableRows = database.tables
    .map(
      (table) => `
      <tr>
        <td>${table.name}</td>
        <td>${table.rows}</td>
        <td>${table.lastWrite}</td>
        <td>${table.size}</td>
      </tr>`
    )
    .join("");

  el("#db-table").innerHTML = tableRows || `<tr><td colspan="4" class="empty-state">No table stats available yet.</td></tr>`;
}

function renderEventGallery(events) {
  if (!events.length) {
    el("#event-gallery").innerHTML = `<p class="empty-state">No events recorded yet.</p>`;
    return;
  }

  el("#event-gallery").innerHTML = events
    .map(
      (event) => `
      <article class="event-card">
        <div class="event-preview">
          <span>No preview</span>
        </div>
        <div class="event-body">
          <div><strong>${formatLocalTime(event.event_ts)}</strong> • ${event.event_type}</div>
          <p>${event.note ?? "No additional details."}</p>
        </div>
      </article>`
    )
    .join("");
}

function renderAlerts(alerts) {
  if (!alerts.length) {
    el("#alerts-list").innerHTML = `<li class="empty-state">No alerts at this time.</li>`;
    return;
  }

  el("#alerts-list").innerHTML = alerts
    .map(
      (item) => `
      <li>
        <span class="alert-severity ${item.severity}">${item.severity}</span>
        ${item.text}
      </li>`
    )
    .join("");
}

function setOverallStatus(data) {
  const status = el("#overall-status");
  status.classList.remove("warn", "bad");

  const temp = data.system.tempC;
  const disk = data.system.diskRemainingGb;
  const failureCount = data.ingest.failure60m ?? 0;
  const deadJobs = data.queue.dead ?? 0;

  if ((temp !== null && temp >= 70) || (disk !== null && disk <= 10) || deadJobs > 0) {
    status.textContent = "Needs attention";
    status.classList.add("bad");
    return;
  }

  if ((temp !== null && temp >= 60) || (disk !== null && disk <= 20) || failureCount >= 8 || data.queue.depth > 30) {
    status.textContent = "Watchlist";
    status.classList.add("warn");
    return;
  }

  status.textContent = "System nominal";
}

function formatLocalTime(value) {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

function buildAlerts(data) {
  const alerts = [];
  const disk = data.system.diskRemainingGb;
  if (disk !== null && disk < 10) {
    alerts.push({ severity: disk < 5 ? "critical" : "warn", text: `Disk running low (${disk.toFixed(1)} GB remaining).` });
  }

  const failures = data.ingest.failure60m ?? 0;
  if (failures >= 10) {
    alerts.push({ severity: "critical", text: `Ingest fail rate high (${failures} failures in the last hour).` });
  } else if (failures >= 5) {
    alerts.push({ severity: "warn", text: `Ingest failures increasing (${failures} in the last hour).` });
  }

  if (data.queue.depth >= 30) {
    alerts.push({ severity: "warn", text: `Worker queue depth is ${data.queue.depth}.` });
  }

  return alerts;
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Request failed: ${path}`);
  }
  return response.json();
}

async function refreshData() {
  try {
    const [systemResp, ingestResp, queueResp, databaseResp, eventsResp, devicesResp] = await Promise.all([
      fetchJson(`${API_BASE}/admin/metrics/system`),
      fetchJson(`${API_BASE}/admin/metrics/ingest`),
      fetchJson(`${API_BASE}/admin/metrics/queue`),
      fetchJson(`${API_BASE}/admin/metrics/database`),
      fetchJson(`${API_BASE}/admin/events?limit=15`),
      fetchJson(`${API_BASE}/admin/devices`),
    ]);

    dashboardData.system = {
      cpu: systemResp.cpu,
      memory: systemResp.memory,
      diskRemainingGb: systemResp.diskRemainingGb,
      tempC: systemResp.tempC,
      uptime: systemResp.uptime,
    };

    dashboardData.ingest = {
      success60m: ingestResp.success_60m ?? 0,
      failure60m: ingestResp.failure_60m ?? 0,
      avgLatencyMs: ingestResp.avg_latency_ms ?? 0,
      series: ingestResp.series ?? createEmptySeries(),
    };

    const queueMetrics = queueResp.queue ?? {};
    dashboardData.queue = {
      depth: queueResp.depth ?? 0,
      running: queueMetrics.running ?? 0,
      failed: queueMetrics.failed ?? 0,
      dead: queueMetrics.dead ?? 0,
      maxVisual: 40,
    };

    dashboardData.database = {
      connected: databaseResp.connected,
      version: databaseResp.version,
      captures: databaseResp.captures,
      events: databaseResp.events,
      jobs: databaseResp.jobs,
      devices: databaseResp.devices,
      ingestAudit: databaseResp.ingestAudit,
      dbSizeMb: databaseResp.dbSizeMb ?? 0,
      tables: databaseResp.tables ?? [],
    };

    dashboardData.events = eventsResp.events ?? [];
    dashboardData.devices = devicesResp.devices ?? [];
    dashboardData.alerts = buildAlerts(dashboardData);
    el("#last-updated").textContent = new Date().toLocaleTimeString();
  } catch (error) {
    console.error("Failed to refresh dashboard data", error);
  } finally {
    render();
  }
}

function render() {
  renderStat("#system-health", [
    ["CPU", formatValue(dashboardData.system.cpu, "%")],
    ["Memory", formatValue(dashboardData.system.memory, "%")],
    ["Disk Free", dashboardData.system.diskRemainingGb !== null ? `${dashboardData.system.diskRemainingGb.toFixed(1)} GB` : "—"],
    ["Temp", formatValue(dashboardData.system.tempC, "°C")],
    ["Uptime", dashboardData.system.uptime],
  ]);

  renderStat("#ingest-metrics", [
    ["Success (60m)", dashboardData.ingest.success60m],
    ["Failures (60m)", dashboardData.ingest.failure60m],
    ["Avg Latency", `${dashboardData.ingest.avgLatencyMs} ms`],
  ]);

  renderStat("#queue-metrics", [
    ["Depth", dashboardData.queue.depth],
    ["Running", dashboardData.queue.running],
    ["Failed", dashboardData.queue.failed],
    ["Dead", dashboardData.queue.dead],
  ]);

  renderChart(dashboardData.ingest.series);
  renderQueueMeter(dashboardData.queue);
  renderDatabase(dashboardData.database);
  renderDevices(dashboardData.devices);
  renderEventGallery(dashboardData.events);
  renderAlerts(dashboardData.alerts);
  setOverallStatus(dashboardData);
}

function setupTabs() {
  const tabs = document.querySelectorAll(".tab");
  const panels = document.querySelectorAll(".tab-panel");

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((other) => {
        other.classList.remove("active");
        other.setAttribute("aria-selected", "false");
      });

      panels.forEach((panel) => panel.classList.remove("active"));

      tab.classList.add("active");
      tab.setAttribute("aria-selected", "true");
      el(`#tab-${tab.dataset.tab}`).classList.add("active");
    });
  });
}

function setupRefreshControls() {
  el("#refresh-btn").addEventListener("click", refreshData);
  const autoRefresh = el("#auto-refresh");

  function startAutoRefresh() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
    }
    refreshTimer = setInterval(refreshData, REFRESH_INTERVAL_MS);
  }

  function stopAutoRefresh() {
    if (refreshTimer) {
      clearInterval(refreshTimer);
      refreshTimer = null;
    }
  }

  autoRefresh.addEventListener("change", () => {
    if (autoRefresh.checked) {
      startAutoRefresh();
    } else {
      stopAutoRefresh();
    }
  });

  if (autoRefresh.checked) {
    startAutoRefresh();
  }
}

function init() {
  setupTabs();
  setupRefreshControls();
  refreshData();
}

init();
