// Single-page dashboard for AI model pricing history.
// Reads pre-computed artifacts from ../pricing_history/.

const PROVIDER_COLORS = {
  anthropic: "#d97706",
  openai: "#10b981",
  google: "#6366f1",
};

const state = {
  series: [],          // raw rows
  models: [],          // lifecycle
  index: null,
  providers: new Set(),
  selectedProviders: new Set(),
  chart: null,
};

// ---- loading ---------------------------------------------------------------

async function loadData() {
  const base = "data";
  const [series, models, index] = await Promise.all([
    fetch(`${base}/series.json`).then(r => r.json()),
    fetch(`${base}/models.json`).then(r => r.json()),
    fetch(`${base}/index.json`).then(r => r.json()),
  ]);
  state.series = series;
  state.models = models;
  state.index = index;
  for (const r of series) state.providers.add(r.provider_id);
  state.selectedProviders = new Set(state.providers);
}

// ---- header / stats --------------------------------------------------------

function fmtDate(d) { return d || "—"; }

function renderHeader() {
  const i = state.index;
  document.getElementById("rangeSummary").textContent =
    `${i.snapshot_count} daily snapshots · ${fmtDate(i.first_date)} → ${fmtDate(i.last_date)} · regenerated ${i.generated_at}`;

  const activeModels = state.models.filter(m => m.currently_active).length;
  const inactiveModels = state.models.filter(m => m.currently_present && !m.currently_active).length;
  const deprecatedInWindow = state.models.filter(m => m.deprecated_on).length;

  const stats = [
    { label: "Providers", value: state.providers.size },
    { label: "Active models", value: activeModels },
    { label: "Inactive (current)", value: inactiveModels },
    { label: "Deprecated in window", value: deprecatedInWindow },
    { label: "Snapshots", value: i.snapshot_count },
  ];
  document.getElementById("stats").innerHTML = stats.map(s =>
    `<div class="stat"><div class="label">${s.label}</div><div class="value">${s.value}</div></div>`
  ).join("");
}

// ---- provider filter chips -------------------------------------------------

function renderProviderChips() {
  const el = document.getElementById("providerFilter");
  el.innerHTML = "";
  for (const pid of [...state.providers].sort()) {
    const chip = document.createElement("span");
    chip.className = "chip on";
    chip.dataset.provider = pid;
    chip.innerHTML = `<span class="swatch" style="background:${PROVIDER_COLORS[pid] || '#888'}"></span>${pid}`;
    chip.addEventListener("click", () => {
      if (state.selectedProviders.has(pid)) {
        state.selectedProviders.delete(pid);
        chip.classList.remove("on");
      } else {
        state.selectedProviders.add(pid);
        chip.classList.add("on");
      }
      renderTrendChart();
    });
    el.appendChild(chip);
  }
}

// ---- weekly bucketing ------------------------------------------------------

function isoWeekKey(dateStr) {
  // YYYY-Www: ISO week's Monday date as the bucket label
  const d = new Date(dateStr + "T00:00:00Z");
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() - day + 1); // back to Monday
  return d.toISOString().slice(0, 10);
}

function bucketed(rows, bucket) {
  if (bucket === "daily") return rows;
  // weekly: keep the latest date per (pricing_id, week)
  const byKey = new Map();
  for (const r of rows) {
    const wk = isoWeekKey(r.date);
    const k = `${r.pricing_id}|${wk}`;
    const prev = byKey.get(k);
    if (!prev || prev.date < r.date) byKey.set(k, { ...r, date: wk });
  }
  return [...byKey.values()];
}

// ---- color per model (stable hash by pricing_id) ---------------------------

function colorForModel(pid, providerId) {
  // Hue within provider's family. Provider sets base; pricing_id varies hue slightly.
  const baseHue = { anthropic: 30, openai: 155, google: 240 }[providerId] ?? 200;
  let h = 0;
  for (let i = 0; i < pid.length; i++) h = (h * 31 + pid.charCodeAt(i)) >>> 0;
  const jitter = (h % 80) - 40; // -40..40
  const sat = 60 + (h % 20);
  const light = 55 + ((h >> 5) % 15);
  return `hsl(${(baseHue + jitter + 360) % 360}, ${sat}%, ${light}%)`;
}

// ---- trend chart -----------------------------------------------------------

function pricingIdsThatChanged(rows, field) {
  const seen = new Map();
  for (const r of rows) {
    const v = r[field];
    if (v == null) continue;
    if (!seen.has(r.pricing_id)) seen.set(r.pricing_id, new Set());
    seen.get(r.pricing_id).add(v);
  }
  const out = new Set();
  for (const [pid, vs] of seen) if (vs.size > 1) out.add(pid);
  return out;
}

function renderTrendChart() {
  const field = document.getElementById("priceField").value;
  const bucket = document.getElementById("bucket").value;
  const yScale = document.getElementById("yScale").value;
  const activeOnly = document.getElementById("activeOnly").checked;
  const changedOnly = document.getElementById("changedOnly").checked;

  let rows = state.series.filter(r => state.selectedProviders.has(r.provider_id));
  if (activeOnly) {
    const activeIds = new Set(state.models.filter(m => m.currently_active).map(m => m.pricing_id));
    rows = rows.filter(r => activeIds.has(r.pricing_id));
  }
  if (changedOnly) {
    const changedIds = pricingIdsThatChanged(rows, field);
    rows = rows.filter(r => changedIds.has(r.pricing_id));
  }

  rows = bucketed(rows, bucket);

  // group into datasets per pricing_id
  const byPid = new Map();
  for (const r of rows) {
    const v = r[field];
    if (v == null) continue;
    if (!byPid.has(r.pricing_id)) byPid.set(r.pricing_id, { row: r, points: [] });
    byPid.get(r.pricing_id).points.push({ x: r.date, y: v });
  }

  const datasets = [];
  for (const [pid, { row, points }] of byPid) {
    points.sort((a, b) => a.x.localeCompare(b.x));
    datasets.push({
      label: `${row.display_name} (${row.provider_id})`,
      data: points,
      borderColor: colorForModel(pid, row.provider_id),
      backgroundColor: colorForModel(pid, row.provider_id),
      borderWidth: 1.5,
      pointRadius: points.length === 1 ? 3 : 1.5,
      pointHoverRadius: 5,
      tension: 0,
      spanGaps: true,
      providerId: row.provider_id,
      pricingId: pid,
    });
  }

  // sort datasets by last price desc so legend lists priciest first
  datasets.sort((a, b) => (b.data.at(-1)?.y ?? 0) - (a.data.at(-1)?.y ?? 0));

  const ctx = document.getElementById("trendChart");
  if (state.chart) state.chart.destroy();
  state.chart = new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      plugins: {
        legend: {
          position: "right",
          labels: {
            color: "#e6e9ef",
            font: { size: 11 },
            boxWidth: 10,
            boxHeight: 10,
            usePointStyle: true,
          },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: $${ctx.parsed.y.toFixed(4)} / 1M tok`,
          },
        },
      },
      scales: {
        x: {
          type: "time",
          time: { unit: bucket === "weekly" ? "week" : "day", tooltipFormat: "yyyy-MM-dd" },
          ticks: { color: "#9aa3b2" },
          grid: { color: "#222730" },
        },
        y: {
          type: yScale,
          ticks: {
            color: "#9aa3b2",
            callback: (v) => `$${v}`,
          },
          grid: { color: "#222730" },
          title: { display: true, text: `${field.replace(/_/g, " ")} (USD per 1M tokens)`, color: "#9aa3b2" },
        },
      },
    },
  });
}

// ---- archive ---------------------------------------------------------------

function renderArchive() {
  const filter = document.getElementById("archiveFilter").value;
  const providerSel = document.getElementById("archiveProvider").value;
  let rows = state.models.slice();

  if (filter === "deprecated") {
    rows = rows.filter(m => m.currently_present && !m.currently_active);
  } else if (filter === "disappeared") {
    rows = rows.filter(m => !m.currently_present);
  } else {
    rows = rows.filter(m => m.deprecated_on || !m.currently_present || m.name_marks_deprecation);
  }
  if (providerSel) rows = rows.filter(m => m.provider_id === providerSel);

  rows.sort((a, b) => {
    const aKey = a.deprecated_on || a.disappeared_after || "";
    const bKey = b.deprecated_on || b.disappeared_after || "";
    return bKey.localeCompare(aKey);
  });

  const tbody = document.getElementById("archiveBody");
  tbody.innerHTML = rows.map(m => `
    <tr>
      <td><span class="tag provider-${m.provider_id}">${m.provider_id}</span></td>
      <td>${m.model_id}</td>
      <td>${m.display_name}</td>
      <td>${fmtDate(m.first_seen)}</td>
      <td>${fmtDate(m.last_active)}</td>
      <td>${fmtDate(m.deprecated_on)}</td>
      <td>${fmtDate(m.last_seen)}</td>
      <td class="num">${m.latest_input != null ? `$${m.latest_input}` : "—"}</td>
      <td class="num">${m.latest_output != null ? `$${m.latest_output}` : "—"}</td>
    </tr>
  `).join("") || `<tr><td colspan="9" style="text-align:center;color:var(--muted);padding:24px">No matching models.</td></tr>`;
}

function populateArchiveProviderSelect() {
  const sel = document.getElementById("archiveProvider");
  for (const pid of [...state.providers].sort()) {
    const opt = document.createElement("option");
    opt.value = pid;
    opt.textContent = pid;
    sel.appendChild(opt);
  }
}

// ---- price changes ---------------------------------------------------------

function detectChanges() {
  const FIELDS = ["input_price", "output_price", "cached_input_price"];
  const byPid = new Map();
  for (const r of state.series) {
    if (!byPid.has(r.pricing_id)) byPid.set(r.pricing_id, []);
    byPid.get(r.pricing_id).push(r);
  }
  const events = [];
  for (const [, rows] of byPid) {
    rows.sort((a, b) => a.date.localeCompare(b.date));
    for (const field of FIELDS) {
      let prev = null;
      let prevDate = null;
      for (const r of rows) {
        const v = r[field];
        if (v == null) continue;
        if (prev != null && v !== prev) {
          events.push({
            date: r.date,
            provider_id: r.provider_id,
            model_id: r.model_id,
            display_name: r.display_name,
            field,
            from: prev,
            to: v,
            delta: v - prev,
            prev_date: prevDate,
          });
        }
        prev = v;
        prevDate = r.date;
      }
    }
  }
  events.sort((a, b) => b.date.localeCompare(a.date));
  return events;
}

function renderChanges() {
  const events = detectChanges();
  const tbody = document.getElementById("changesBody");
  if (!events.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">No price changes detected in the snapshot window — pricing has been stable.</td></tr>`;
    return;
  }
  tbody.innerHTML = events.map(e => {
    const pct = e.from ? ((e.delta / e.from) * 100).toFixed(1) : "∞";
    const sign = e.delta > 0 ? "+" : "";
    return `
      <tr>
        <td>${e.date}</td>
        <td><span class="tag provider-${e.provider_id}">${e.provider_id}</span></td>
        <td>${e.display_name} <span class="tag">${e.model_id}</span></td>
        <td>${e.field.replace(/_/g, " ")}</td>
        <td class="num">$${e.from}</td>
        <td class="num">$${e.to}</td>
        <td class="num">${sign}${e.delta.toFixed(4)} (${sign}${pct}%)</td>
      </tr>
    `;
  }).join("");
}

// ---- tabs ------------------------------------------------------------------

function setupTabs() {
  document.querySelectorAll(".tab").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
      if (btn.dataset.tab === "trends") renderTrendChart();
    });
  });
}

function bindControls() {
  ["priceField", "bucket", "yScale", "activeOnly", "changedOnly"].forEach(id => {
    document.getElementById(id).addEventListener("change", renderTrendChart);
  });
  ["archiveFilter", "archiveProvider"].forEach(id => {
    document.getElementById(id).addEventListener("change", renderArchive);
  });
}

// ---- init ------------------------------------------------------------------

async function main() {
  try {
    await loadData();
  } catch (e) {
    document.getElementById("rangeSummary").textContent =
      "Failed to load pricing_history/*.json. Did you run scripts/build_dashboard_data.py --rebuild?";
    console.error(e);
    return;
  }
  renderHeader();
  renderProviderChips();
  populateArchiveProviderSelect();
  setupTabs();
  bindControls();
  renderTrendChart();
  renderArchive();
  renderChanges();
}

main();
