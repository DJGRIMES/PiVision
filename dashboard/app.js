const dashboardData = {
  system: {
    cpu: 22,
    memory: 58,
    diskRemainingGb: 86,
    tempC: 52,
    uptime: "4d 12h",
  },
  ingest: {
    success60m: 114,
    failure60m: 3,
    avgLatencyMs: 174,
    series: [9, 8, 10, 12, 11, 7, 9, 10, 14, 8, 6, 10],
  },
  queue: {
    depth: 6,
    running: 2,
    failed: 1,
    dead: 0,
    maxVisual: 40,
  },
  database: {
    connected: true,
    version: "PostgreSQL 16",
    writeLatencyMs: 8,
    vacuumAgeH: 10,
    dbSizeMb: 412,
    eventsSizeMb: 188,
    imagesSizeMb: 1660,
    queueSizeMb: 43,
    tables: [
      { name: "captures", rows: 45230, lastWrite: "8s ago", size: "96 MB" },
      { name: "events", rows: 3912, lastWrite: "19s ago", size: "188 MB" },
      { name: "jobs", rows: 22810, lastWrite: "4s ago", size: "43 MB" },
      { name: "devices", rows: 6, lastWrite: "2m ago", size: "1 MB" },
    ],
  },
  devices: [
    { id: "camera-01", lastSeen: "14s ago", rssi: "-62 dBm", battery: "4.95v", fw: "1.1.3" },
    { id: "camera-02", lastSeen: "44s ago", rssi: "-59 dBm", battery: "5.01v", fw: "1.1.3" },
  ],
  events: [
    {
      ts: "10:22:04",
      type: "interaction_detected",
      note: "Shelf front ROI changed above threshold.",
      image: "https://picsum.photos/seed/pivision-event-1/480/240",
    },
    {
      ts: "10:18:41",
      type: "interaction_detected",
      note: "Motion in reach zone sustained for 4 frames.",
      image: "https://picsum.photos/seed/pivision-event-2/480/240",
    },
    {
      ts: "10:16:09",
      type: "system",
      note: "Worker restarted after deploy (healthy).",
      image: "https://picsum.photos/seed/pivision-event-3/480/240",
    },
  ],
  alerts: [
    "Low disk threshold: trigger warning under 20 GB remaining.",
    "Device offline threshold: no heartbeat for > 2 minutes.",
    "Queue risk: queued jobs > 30 for more than 5 minutes.",
    "Error burst: ingest failures >= 10 over rolling 10 minutes.",
  ],
};

const el = (selector) => document.querySelector(selector);

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
  const max = Math.max(...series);
  chart.innerHTML = series
    .map((n) => `<div class="bar" style="height:${Math.max(10, Math.round((n / max) * 100))}%"></div>`)
    .join("");
}

function renderDevices(devices) {
  const rows = devices
    .map(
      (d) => `
      <tr>
        <td>${d.id}</td>
        <td>${d.lastSeen}</td>
        <td>${d.rssi}</td>
        <td>${d.battery}</td>
        <td>${d.fw}</td>
      </tr>`
    )
    .join("");

  el("#device-table").innerHTML = rows;
}

function renderQueueMeter(queue) {
  const pct = Math.min(100, Math.round((queue.depth / queue.maxVisual) * 100));
  el("#queue-bar").style.width = `${pct}%`;
}

function renderDatabase(database) {
  renderStat("#db-health", [
    ["Connected", database.connected ? "Yes" : "No"],
    ["Version", database.version],
    ["Write Latency", `${database.writeLatencyMs} ms`],
    ["Vacuum Age", `${database.vacuumAgeH} h`],
  ]);

  renderStat("#db-storage", [
    ["DB Size", `${database.dbSizeMb} MB`],
    ["Events", `${database.eventsSizeMb} MB`],
    ["Images", `${database.imagesSizeMb} MB`],
    ["Queue", `${database.queueSizeMb} MB`],
  ]);

  el("#db-table").innerHTML = database.tables
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
}

function renderEventGallery(events) {
  el("#event-gallery").innerHTML = events
    .map(
      (event) => `
      <article class="event-card">
        <img src="${event.image}" alt="${event.type} at ${event.ts}" loading="lazy" />
        <div class="event-body">
          <div><strong>${event.ts}</strong> • ${event.type}</div>
          <p>${event.note}</p>
        </div>
      </article>`
    )
    .join("");
}

function renderAlerts(alerts) {
  el("#alerts-list").innerHTML = alerts.map((item) => `<li>${item}</li>`).join("");
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

function render() {
  renderStat("#system-health", [
    ["CPU", `${dashboardData.system.cpu}%`],
    ["Memory", `${dashboardData.system.memory}%`],
    ["Disk Free", `${dashboardData.system.diskRemainingGb} GB`],
    ["Temp", `${dashboardData.system.tempC}°C`],
    ["Uptime", dashboardData.system.uptime],
  ]);

  renderStat("#ingest-metrics", [
    ["Success", dashboardData.ingest.success60m],
    ["Fail", dashboardData.ingest.failure60m],
    ["Latency", `${dashboardData.ingest.avgLatencyMs} ms`],
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
  setupTabs();
}

render();
