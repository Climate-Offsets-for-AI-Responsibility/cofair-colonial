// Single-page dashboard for AI model pricing history.
// Reads pre-computed artifacts from ./data/ (see scripts/build_dashboard_data.py).
//
// Markup uses @cofair/ui's own `cofair-*` classes from vendor/cofair-ui.css, and
// every color the chart draws comes from a `--cofair-*` token — nothing visual
// is hard-coded here (hub R13).

import {
  contextsNeedDisambiguation,
  effectiveModality,
  formatLegendLabel,
  isFutureSchedulePlaceholder,
  sortDatasetsForDate,
} from "./labels.js";

const Chart = window.Chart;

const SERIES_COUNT = 6; // --cofair-dataviz-1 … -6
const MODALITY_ORDER = ["text", "multimodal", "audio", "image"];

// Badge variants matched to the data-viz steps so a provider's badge and its
// chart lines read as the same color.
const SERIES_BADGE = [
  "cofair-badge--primary",
  "cofair-badge--accent",
  "cofair-badge--warning",
  "cofair-badge--success",
  "cofair-badge--danger",
  "",
];

const state = {
  series: [],          // raw rows
  models: [],          // lifecycle
  index: null,
  providers: new Set(),
  selectedProviders: new Set(),
  modalities: new Set(),
  selectedModalities: new Set(),
  providerSeries: new Map(), // provider_id → 0-based data-viz step
  chart: null,
  hoverDate: null,
  hoverPricingId: null,
};

// ---- tokens ----------------------------------------------------------------

/** Read the live token values so the chart follows the active theme. */
function theme() {
  const style = getComputedStyle(document.documentElement);
  const token = (name) => style.getPropertyValue(name).trim();
  const rootSize = parseFloat(style.fontSize) || 16;
  const px = (name) => {
    const raw = token(name);
    return raw.endsWith("rem") ? parseFloat(raw) * rootSize : parseFloat(raw);
  };
  return {
    text: token("--cofair-color-text"),
    muted: token("--cofair-color-text-muted"),
    grid: token("--cofair-color-border-subtle"),
    surface: token("--cofair-color-bg-elevated"),
    border: token("--cofair-color-border"),
    sans: token("--cofair-font-sans"),
    mono: token("--cofair-font-mono"),
    sizeXs: px("--cofair-font-size-xs"),
    series: Array.from({ length: SERIES_COUNT }, (_, i) =>
      token(`--cofair-dataviz-${i + 1}`),
    ),
  };
}

function hexToHsl(hex) {
  const raw = hex.replace("#", "").trim();
  const full = raw.length === 3 ? raw.split("").map((c) => c + c).join("") : raw;
  const r = parseInt(full.slice(0, 2), 16) / 255;
  const g = parseInt(full.slice(2, 4), 16) / 255;
  const b = parseInt(full.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  let h = 0;
  let s = 0;
  if (d !== 0) {
    s = d / (1 - Math.abs(2 * l - 1));
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  return { h, s: s * 100, l: l * 100 };
}

function hash(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  return h;
}

/**
 * One line per model, tinted within its provider's brand color.
 *
 * Lightness carries the per-model variation (deterministic in `pricing_id`), so
 * every line stays inside the provider's hue and the palette stays on-brand.
 */
function seriesColor(pricingId, providerId, palette) {
  const step = state.providerSeries.get(providerId) ?? 0;
  const raw = palette.series[step % SERIES_COUNT] || palette.muted;
  if (!raw.startsWith("#")) return raw || "currentColor";
  const base = hexToHsl(raw);
  const spread = ((hash(pricingId) % 2001) / 1000 - 1) * 12; // -12…+12
  const l = Math.min(82, Math.max(18, base.l + spread));
  return `hsl(${base.h.toFixed(1)}, ${base.s.toFixed(1)}%, ${l.toFixed(1)}%)`;
}

// ---- helpers ---------------------------------------------------------------

/** Snapshot data is scraped from third-party sites — never inject it raw. */
function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function fmtDate(d) { return d || "—"; }

function providerBadge(providerId) {
  const step = state.providerSeries.get(providerId) ?? 0;
  const variant = SERIES_BADGE[step % SERIES_BADGE.length];
  return `<span class="cofair-badge ${variant}">${esc(providerId)}</span>`;
}

function emptyRow(colspan, title, body) {
  return `<tr class="cofair-table__row">
    <td class="cofair-table__td" colspan="${colspan}">
      <div class="empty-state">
        <p class="empty-state__title">${esc(title)}</p>
        <p class="empty-state__body cofair-text cofair-text--small">${esc(body)}</p>
      </div>
    </td>
  </tr>`;
}

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
  for (const r of series) {
    state.providers.add(r.provider_id);
    state.modalities.add(effectiveModality(r.modality));
  }
  state.selectedProviders = new Set(state.providers);
  state.selectedModalities = new Set(state.modalities);
  [...state.providers].sort().forEach((pid, i) => state.providerSeries.set(pid, i));
}

// ---- header / stats --------------------------------------------------------

function renderHeader() {
  const i = state.index;
  document.getElementById("rangeSummary").textContent =
    `${i.snapshot_count} daily snapshots · ${fmtDate(i.first_date)} → ${fmtDate(i.last_date)} · regenerated ${i.generated_at}`;
}

// ---- provider filter chips -------------------------------------------------

function renderProviderChips() {
  const el = document.getElementById("providerFilter");
  el.innerHTML = "";
  for (const pid of [...state.providers].sort()) {
    const step = state.providerSeries.get(pid) ?? 0;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `chip cofair-badge ${SERIES_BADGE[step % SERIES_BADGE.length]}`;
    chip.dataset.provider = pid;
    chip.dataset.series = String((step % SERIES_COUNT) + 1);
    chip.setAttribute("aria-pressed", "true");
    chip.innerHTML = `<span class="chip__swatch"></span>${esc(pid)}`;
    chip.addEventListener("click", () => {
      const on = state.selectedProviders.has(pid);
      if (on) state.selectedProviders.delete(pid);
      else state.selectedProviders.add(pid);
      chip.setAttribute("aria-pressed", String(!on));
      renderTrendChart();
    });
    el.appendChild(chip);
  }
}

function renderModalityChips() {
  const el = document.getElementById("modalityFilter");
  el.innerHTML = "";
  for (const mod of MODALITY_ORDER) {
    if (!state.modalities.has(mod)) continue;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip cofair-badge";
    chip.dataset.modality = mod;
    const on = state.selectedModalities.has(mod);
    chip.setAttribute("aria-pressed", String(on));
    chip.textContent = mod.charAt(0).toUpperCase() + mod.slice(1);
    chip.addEventListener("click", () => {
      const selected = state.selectedModalities.has(mod);
      if (selected) state.selectedModalities.delete(mod);
      else state.selectedModalities.add(mod);
      chip.setAttribute("aria-pressed", String(!selected));
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

function pointDate(point) {
  if (!point) return null;
  if (typeof point.x === "string") return point.x.slice(0, 10);
  const d = new Date(point.x);
  return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
}

function clearChartHover() {
  if (!state.hoverDate && !state.hoverPricingId) return;
  state.hoverDate = null;
  state.hoverPricingId = null;
  if (state.chart) {
    renderLegend(state.chart);
    state.chart.draw();
  }
}

const dateMarkerPlugin = {
  id: "dateMarker",
  afterDraw(chart) {
    if (!state.hoverDate) return;
    const xScale = chart.scales.x;
    if (!xScale) return;
    const x = xScale.getPixelForValue(state.hoverDate + "T00:00:00Z");
    const { top, bottom, left, right } = chart.chartArea;
    if (x < left || x > right) return;
    const palette = theme();
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = palette.muted;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.restore();
  },
};

function renderLegend(chart) {
  const header = document.getElementById("trendLegendHeader");
  const list = document.getElementById("trendLegendList");
  const datasets = chart.data.datasets;
  const ordered = state.hoverDate
    ? sortDatasetsForDate(datasets, state.hoverDate)
    : [...datasets].sort((a, b) => (b.data.at(-1)?.y ?? 0) - (a.data.at(-1)?.y ?? 0));

  if (state.hoverDate) {
    header.hidden = false;
    header.textContent = state.hoverDate;
  } else {
    header.hidden = true;
    header.textContent = "";
  }

  list.replaceChildren();
  for (const ds of ordered) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chart-legend__item";
    if (ds.hidden) btn.classList.add("chart-legend__item--hidden");
    if (state.hoverPricingId && ds.pricingId === state.hoverPricingId) {
      btn.classList.add("chart-legend__item--active");
    }
    const swatch = document.createElement("span");
    swatch.className = "chart-legend__swatch";
    swatch.style.background = ds.borderColor;
    const label = document.createElement("span");
    label.textContent = ds.legendLabel;
    btn.append(swatch, label);
    btn.addEventListener("click", () => {
      ds.hidden = !ds.hidden;
      chart.update();
      renderLegend(chart);
    });
    li.appendChild(btn);
    list.appendChild(li);
  }
}

function onChartHover(evt, _elements, chart) {
  if (!evt || evt.type === "mouseout" || evt.x == null) {
    clearChartHover();
    return;
  }
  const { left, right, top, bottom } = chart.chartArea;
  if (evt.x < left || evt.x > right || evt.y < top || evt.y > bottom) {
    clearChartHover();
    return;
  }

  const alongX = chart.getElementsAtEventForMode(
    evt, "nearest", { intersect: false, axis: "x" }, false,
  );
  const onNode = chart.getElementsAtEventForMode(
    evt, "nearest", { intersect: true }, false,
  );

  let date = null;
  if (alongX[0]) {
    const ds = chart.data.datasets[alongX[0].datasetIndex];
    date = pointDate(ds.data[alongX[0].index]);
  }
  const pricingId = onNode[0]
    ? chart.data.datasets[onNode[0].datasetIndex].pricingId
    : null;

  if (date === state.hoverDate && pricingId === state.hoverPricingId) return;
  state.hoverDate = date;
  state.hoverPricingId = pricingId;
  renderLegend(chart);
  chart.draw();
}

function renderTrendChart() {
  const field = document.getElementById("priceField").value;
  const bucket = document.getElementById("bucket").value;
  const yScale = document.getElementById("yScale").value;
  const activeOnly = document.getElementById("activeOnly").checked;
  const changedOnly = document.getElementById("changedOnly").checked;
  const palette = theme();

  state.hoverDate = null;
  state.hoverPricingId = null;

  let rows = state.series.filter(r =>
    state.selectedProviders.has(r.provider_id)
    && state.selectedModalities.has(effectiveModality(r.modality))
    && !isFutureSchedulePlaceholder(r),
  );
  if (activeOnly) {
    const activeIds = new Set(state.models.filter(m => m.currently_active).map(m => m.pricing_id));
    rows = rows.filter(r => activeIds.has(r.pricing_id));
  }
  if (changedOnly) {
    const changedIds = pricingIdsThatChanged(rows, field);
    rows = rows.filter(r => changedIds.has(r.pricing_id));
  }

  rows = bucketed(rows, bucket);

  const byPid = new Map();
  for (const r of rows) {
    const v = r[field];
    if (v == null) continue;
    if (!byPid.has(r.pricing_id)) byPid.set(r.pricing_id, { row: r, points: [] });
    byPid.get(r.pricing_id).points.push({ x: r.date, y: v });
  }

  const heads = [...byPid.values()].map(({ row }) => row);
  const needContext = contextsNeedDisambiguation(heads);

  const datasets = [];
  for (const [pid, { row, points }] of byPid) {
    points.sort((a, b) => a.x.localeCompare(b.x));
    const color = seriesColor(pid, row.provider_id, palette);
    const showContext = needContext.has(`${row.provider_id}|${row.model_id}`);
    datasets.push({
      label: formatLegendLabel(row, { showContext }),
      legendLabel: formatLegendLabel(row, { showContext }),
      data: points,
      borderColor: color,
      backgroundColor: color,
      borderWidth: 1.5,
      pointRadius: points.length === 1 ? 3 : 1.5,
      pointHoverRadius: 5,
      pointHitRadius: 8,
      tension: 0,
      spanGaps: true,
      providerId: row.provider_id,
      pricingId: pid,
    });
  }

  datasets.sort((a, b) => (b.data.at(-1)?.y ?? 0) - (a.data.at(-1)?.y ?? 0));

  const ctx = document.getElementById("trendChart");
  if (state.chart) state.chart.destroy();
  state.chart = new Chart(ctx, {
    type: "line",
    data: { datasets },
    plugins: [dateMarkerPlugin],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      onHover: onChartHover,
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false },
      },
      scales: {
        x: {
          type: "time",
          time: { unit: bucket === "weekly" ? "week" : "day" },
          ticks: { color: palette.muted, font: { family: palette.mono, size: palette.sizeXs } },
          grid: { color: palette.grid },
        },
        y: {
          type: yScale,
          ticks: {
            color: palette.muted,
            font: { family: palette.mono, size: palette.sizeXs },
            callback: (v) => `$${v}`,
          },
          grid: { color: palette.grid },
          title: {
            display: true,
            text: `${field.replace(/_/g, " ")} (USD per 1M tokens)`,
            color: palette.muted,
            font: { family: palette.sans, size: palette.sizeXs },
          },
        },
      },
    },
  });

  renderLegend(state.chart);
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
    <tr class="cofair-table__row">
      <td class="cofair-table__td">${providerBadge(m.provider_id)}</td>
      <td class="cofair-table__td">${esc(m.model_id)}</td>
      <td class="cofair-table__td">${esc(m.display_name)}</td>
      <td class="cofair-table__td">${esc(fmtDate(m.first_seen))}</td>
      <td class="cofair-table__td">${esc(fmtDate(m.last_active))}</td>
      <td class="cofair-table__td">${esc(fmtDate(m.deprecated_on))}</td>
      <td class="cofair-table__td">${esc(fmtDate(m.last_seen))}</td>
      <td class="cofair-table__td cofair-table__td--num">${m.latest_input != null ? `$${esc(m.latest_input)}` : "—"}</td>
      <td class="cofair-table__td cofair-table__td--num">${m.latest_output != null ? `$${esc(m.latest_output)}` : "—"}</td>
    </tr>
  `).join("") || emptyRow(9, "No matching models", "Try a different filter or provider.");
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
    tbody.innerHTML = emptyRow(
      7,
      "No price changes detected",
      "List prices have been stable across the whole snapshot window.",
    );
    return;
  }
  tbody.innerHTML = events.map(e => {
    const pct = e.from ? ((e.delta / e.from) * 100).toFixed(1) : "∞";
    const sign = e.delta > 0 ? "+" : "";
    const dir = e.delta > 0 ? "delta--up" : "delta--down";
    return `
      <tr class="cofair-table__row">
        <td class="cofair-table__td">${esc(e.date)}</td>
        <td class="cofair-table__td">${providerBadge(e.provider_id)}</td>
        <td class="cofair-table__td">${esc(e.display_name)} <span class="cofair-badge">${esc(e.model_id)}</span></td>
        <td class="cofair-table__td">${esc(e.field.replace(/_/g, " "))}</td>
        <td class="cofair-table__td cofair-table__td--num">$${esc(e.from)}</td>
        <td class="cofair-table__td cofair-table__td--num">$${esc(e.to)}</td>
        <td class="cofair-table__td cofair-table__td--num ${dir}">${sign}${e.delta.toFixed(4)} (${sign}${pct}%)</td>
      </tr>
    `;
  }).join("");
}

// ---- tabs ------------------------------------------------------------------

/** Mirrors @cofair/ui's Tabs: roving tabindex plus arrow-key navigation. */
function setupTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];

  const activate = (tab) => {
    for (const t of tabs) {
      const selected = t === tab;
      t.classList.toggle("cofair-tabs__tab--active", selected);
      t.setAttribute("aria-selected", String(selected));
      t.tabIndex = selected ? 0 : -1;
      const panel = document.getElementById(t.getAttribute("aria-controls"));
      if (panel) panel.hidden = !selected;
    }
    if (tab.dataset.tab === "trends") renderTrendChart();
  };

  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      e.preventDefault();
      const next = e.key === "ArrowRight"
        ? (i + 1) % tabs.length
        : (i - 1 + tabs.length) % tabs.length;
      tabs[next].focus();
      activate(tabs[next]);
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
      "Failed to load data/*.json. Did you run scripts/build_dashboard_data.py --rebuild?";
    console.error(e);
    return;
  }
  renderHeader();
  renderProviderChips();
  renderModalityChips();
  populateArchiveProviderSelect();
  setupTabs();
  bindControls();
  document.getElementById("trendChart").addEventListener("mouseleave", clearChartHover);
  renderTrendChart();
  renderArchive();
  renderChanges();
}

main();
