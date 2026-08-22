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
  hexToHsl,
  isFutureSchedulePlaceholder,
  priceAtOrBefore,
  sortDatasetsForDate,
} from "./labels.js";

const Chart = window.Chart;

const SERIES_COUNT = 7; // --cofair-dataviz-1 … -7
const MODALITY_ORDER = ["text", "multimodal", "audio", "image"];

// Keep stable provider tinting across refreshes regardless of provider sort.
// Steps map to --cofair-dataviz-1…7 (see styles.css overrides for 2–7). Every
// provider the scrape produces is pinned here: with seven providers and seven
// steps, an unpinned one would collide with a pinned one.
const PREFERRED_PROVIDER_SERIES = new Map([
  ["openai", 0], // forest
  ["anthropic", 1], // burnt orange
  ["google", 2], // blue
  ["aws", 3], // tan
  ["deepseek", 4], // magenta
  ["qwen", 5], // purple
  ["xai", 6], // neutral
]);

// Display-only relabels: the immutable snapshot contract keeps provider_id
// "aws" for historical continuity; surface shows lowercase "amazon".
const PROVIDER_LABELS = new Map([["aws", "amazon"]]);

function providerLabel(providerId) {
  return PROVIDER_LABELS.get(providerId) || providerId;
}

const state = {
  series: [],          // raw rows
  models: [],          // lifecycle
  index: null,
  equivalence: null,
  providers: new Set(),
  // One provider-visibility model per surface: provider_id → "solo" | "hidden".
  // Absent means "shown alongside the rest", so an untouched map shows everything.
  // Same three-position interaction as the trend chart's legend.
  providerModes: {
    trend: new Map(),
    archive: new Map(),
    changes: new Map(),
  },
  modalities: new Set(),
  selectedModalities: new Set(),
  providerSeries: new Map(), // provider_id → 0-based data-viz step
  chart: null,
  // Resting state is the most recent scraped day rather than nothing, so the
  // legend opens on a real column of prices. No series is selected at rest —
  // the highlight is reserved for an actual rollover.
  hoverDate: null,
  hoverPricingId: null,
  defaultHoverDate: null,
  legendMode: new Map(), // pricing_id → "solo" | "hidden"
  priceField: "output_price",
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

const DATE_FMT = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
});
const DATE_FMT_UTC = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

const DATETIME_FMT = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

const MONEY_FMT = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

function parseDateValue(value) {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  const str = String(value);
  const parsed = /^\d{4}-\d{2}-\d{2}$/.test(str)
    ? new Date(`${str}T00:00:00Z`)
    : new Date(str);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function fmtDate(value) {
  if (!value) return "—";
  const str = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(str)) {
    const parsedUtc = new Date(`${str}T00:00:00Z`);
    return Number.isNaN(parsedUtc.getTime()) ? str : DATE_FMT_UTC.format(parsedUtc);
  }
  const parsed = parseDateValue(value);
  return parsed ? DATE_FMT.format(parsed) : str;
}

function fmtDateTime(value) {
  const parsed = parseDateValue(value);
  return parsed ? DATETIME_FMT.format(parsed) : (value || "—");
}

function providerBadge(providerId) {
  const step = state.providerSeries.get(providerId) ?? 0;
  const series = (step % SERIES_COUNT) + 1;
  return `<span class="cofair-badge" data-series="${series}">${esc(providerLabel(providerId))}</span>`;
}

function assignProviderSeries() {
  state.providerSeries = new Map();
  const used = new Set();

  for (const [providerId, step] of PREFERRED_PROVIDER_SERIES.entries()) {
    if (!state.providers.has(providerId)) continue;
    const normalized = step % SERIES_COUNT;
    state.providerSeries.set(providerId, normalized);
    used.add(normalized);
  }

  let next = 0;
  for (const providerId of [...state.providers].sort()) {
    if (state.providerSeries.has(providerId)) continue;

    let step = next % SERIES_COUNT;
    if (used.size < SERIES_COUNT) {
      while (used.has(step)) {
        next += 1;
        step = next % SERIES_COUNT;
      }
      used.add(step);
    }

    state.providerSeries.set(providerId, step);
    next += 1;
  }
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
  const [series, models, index, equivalence] = await Promise.all([
    fetch(`${base}/series.json`).then(r => r.json()),
    fetch(`${base}/models.json`).then(r => r.json()),
    fetch(`${base}/index.json`).then(r => r.json()),
    fetch(`${base}/equivalence.json`).then(r => r.json()),
  ]);
  state.series = series;
  state.models = models;
  state.index = index;
  state.equivalence = equivalence;
  for (const r of series) {
    state.providers.add(r.provider_id);
    state.modalities.add(effectiveModality(r.modality));
  }
  state.selectedModalities = new Set(state.modalities);
  assignProviderSeries();
}

function fmtUsd(value) {
  if (value == null || Number.isNaN(value)) return "—";
  if (Math.abs(value) < 0.01) return `$${value.toFixed(4)}`;
  return MONEY_FMT.format(value);
}

// ---- header / stats --------------------------------------------------------

function renderHeader() {
  const i = state.index;
  const rangeSummary = document.getElementById("rangeSummary");
  if (!rangeSummary) return;
  rangeSummary.textContent =
    `${i.snapshot_count} daily snapshots · ${fmtDate(i.first_date)} → ${fmtDate(i.last_date)} · regenerated ${fmtDateTime(i.generated_at)}`;
}

// ---- provider filter chips -------------------------------------------------

function soloProvider(scope) {
  for (const [pid, mode] of state.providerModes[scope]) {
    if (mode === "solo") return pid;
  }
  return null;
}

function providerVisible(scope, providerId) {
  const solo = soloProvider(scope);
  if (solo) return providerId === solo;
  return state.providerModes[scope].get(providerId) !== "hidden";
}

/**
 * none → solo → hidden → none, with at most one provider soloed per surface.
 *
 * While a provider is isolated every other chip reads as "not shown", so a
 * click on one of them means "isolate this one instead" — whatever mode that
 * chip happened to be left in before the isolation took effect.
 */
function cycleProviderMode(scope, providerId) {
  const modes = state.providerModes[scope];
  const solo = soloProvider(scope);
  if (solo && solo !== providerId) {
    modes.delete(solo);
    modes.set(providerId, "solo");
    return;
  }
  const mode = modes.get(providerId);
  if (!mode) {
    modes.set(providerId, "solo");
    return;
  }
  if (mode === "solo") {
    modes.set(providerId, "hidden");
    return;
  }
  modes.delete(providerId);
}

/**
 * Push a surface's visibility model onto its chips.
 *
 * Separate from construction because soloing one provider changes how every
 * *other* chip in the row reads, so a click cannot just restyle the chip that
 * was clicked.
 */
function syncProviderChipRow(el, scope) {
  const solo = soloProvider(scope);
  el.querySelectorAll(".chip[data-provider]").forEach((chip) => {
    const pid = chip.dataset.provider;
    const mode = state.providerModes[scope].get(pid) || "";
    chip.dataset.mode = mode;
    chip.setAttribute("aria-pressed", String(providerVisible(scope, pid)));
    // State never rests on styling alone: say which of the three positions the
    // chip is in and what clicking it does next.
    const position =
      mode === "solo"
        ? "isolated — click to hide"
        : mode === "hidden"
          ? "hidden — click to restore"
          : solo
            ? "hidden while another provider is isolated — click to isolate"
            : "shown — click to isolate";
    chip.setAttribute("aria-label", `${providerLabel(pid)}: ${position}`);
  });
}

function renderProviderChipRow(elementId, scope, onChange) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.innerHTML = "";
  for (const pid of [...state.providers].sort()) {
    const step = state.providerSeries.get(pid) ?? 0;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip cofair-badge";
    chip.dataset.provider = pid;
    chip.dataset.series = String((step % SERIES_COUNT) + 1);
    chip.innerHTML = `<span class="chip__swatch"></span>${esc(providerLabel(pid))}`;
    chip.addEventListener("click", () => {
      cycleProviderMode(scope, pid);
      syncProviderChipRow(el, scope);
      onChange();
    });
    el.appendChild(chip);
  }
  syncProviderChipRow(el, scope);
}

function renderProviderChips() {
  renderProviderChipRow("providerFilter", "trend", renderTrendChart);
}

function renderArchiveProviderChips() {
  renderProviderChipRow("archiveProviderFilter", "archive", renderArchive);
}

function renderChangesProviderChips() {
  renderProviderChipRow("changesProviderFilter", "changes", renderChanges);
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

function soloLegendPricingId() {
  for (const [pricingId, mode] of state.legendMode) {
    if (mode === "solo") return pricingId;
  }
  return null;
}

function pruneLegendMode(pricingIds) {
  for (const pricingId of state.legendMode.keys()) {
    if (!pricingIds.has(pricingId)) state.legendMode.delete(pricingId);
  }
}

function cycleLegendMode(pricingId) {
  const mode = state.legendMode.get(pricingId);
  if (!mode) {
    for (const [id, value] of state.legendMode) {
      if (value === "solo") state.legendMode.delete(id);
    }
    state.legendMode.set(pricingId, "solo");
    return;
  }
  if (mode === "solo") {
    state.legendMode.set(pricingId, "hidden");
    return;
  }
  state.legendMode.delete(pricingId);
}

function applyLegendVisibility(chart) {
  const soloId = soloLegendPricingId();
  const hiddenIds = new Set(
    [...state.legendMode.entries()]
      .filter(([, mode]) => mode === "hidden")
      .map(([pricingId]) => pricingId),
  );
  for (const ds of chart.data.datasets) {
    if (soloId) ds.hidden = ds.pricingId !== soloId;
    else ds.hidden = hiddenIds.has(ds.pricingId);
  }
}

function hideNodeTooltip() {
  const tooltip = document.getElementById("chartNodeTooltip");
  if (!tooltip) return;
  tooltip.hidden = true;
}

function showNodeTooltip(chart, hit, priceField) {
  const tooltip = document.getElementById("chartNodeTooltip");
  if (!tooltip || !hit) return;

  const ds = chart.data.datasets[hit.datasetIndex];
  const point = ds.data[hit.index];
  if (!point) return;

  const date = pointDate(point);
  const element = hit.element;
  const canvas = chart.canvas;
  const plot = canvas.parentElement;
  if (!plot || element?.x == null || element?.y == null) return;

  const provider = tooltip.querySelector(".chart-node-tooltip__provider");
  const model = tooltip.querySelector(".chart-node-tooltip__model");
  const meta = tooltip.querySelector(".chart-node-tooltip__meta");
  if (!provider || !model || !meta) return;

  provider.textContent = providerLabel(ds.providerId);
  model.textContent = ds.legendLabel;
  const fieldLabel = priceField.replace(/_/g, " ");
  meta.textContent = `${fmtDate(date)} · ${fieldLabel} ${fmtUsd(point.y)}`;

  const plotRect = plot.getBoundingClientRect();
  const canvasRect = canvas.getBoundingClientRect();
  const x = canvasRect.left - plotRect.left + element.x;
  tooltip.style.top = `${canvasRect.top - plotRect.top + element.y}px`;
  tooltip.hidden = false;
  // The card is centred on the node, so a node near either edge pushes half of
  // it outside the plot and the text reflows to one word per line. Measure at a
  // position with room, then clamp so the card stays inside.
  tooltip.style.left = "0px";
  const half = tooltip.offsetWidth / 2;
  tooltip.style.left = `${Math.min(Math.max(x, half), plotRect.width - half)}px`;
}

/** Leaving the plot returns to the resting state: latest day, nothing selected. */
function clearChartHover() {
  hideNodeTooltip();
  if (state.hoverDate === state.defaultHoverDate && !state.hoverPricingId) return;
  state.hoverDate = state.defaultHoverDate;
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
    header.textContent = fmtDate(state.hoverDate);
  } else {
    header.hidden = true;
    header.textContent = "";
  }

  const soloId = soloLegendPricingId();
  list.replaceChildren();
  for (const ds of ordered) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chart-legend__item";
    const mode = state.legendMode.get(ds.pricingId);
    if (mode === "hidden") btn.classList.add("chart-legend__item--hidden");
    if (mode === "solo") btn.classList.add("chart-legend__item--solo");
    if (soloId && ds.pricingId !== soloId && mode !== "hidden") {
      btn.classList.add("chart-legend__item--dimmed");
    }
    if (state.hoverPricingId && ds.pricingId === state.hoverPricingId) {
      btn.classList.add("chart-legend__item--active");
    }
    const swatch = document.createElement("span");
    swatch.className = "chart-legend__swatch";
    swatch.style.background = ds.borderColor;
    const label = document.createElement("span");
    const hoverPrice = state.hoverDate
      ? priceAtOrBefore(ds.data, state.hoverDate)
      : null;
    label.textContent = hoverPrice == null
      ? ds.legendLabel
      : `${ds.legendLabel} · ${fmtUsd(hoverPrice)}`;
    btn.append(swatch, label);
    btn.addEventListener("click", () => {
      cycleLegendMode(ds.pricingId);
      applyLegendVisibility(chart);
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

  if (onNode[0]) showNodeTooltip(chart, onNode[0], state.priceField);
  else hideNodeTooltip();

  if (date === state.hoverDate && pricingId === state.hoverPricingId) return;
  state.hoverDate = date;
  state.hoverPricingId = pricingId;
  renderLegend(chart);
  chart.draw();
}

function renderTrendChart() {
  const field = document.getElementById("priceField").value;
  const yScale = document.getElementById("yScale").value;
  const activeOnly = document.getElementById("activeOnly").checked;
  const changedOnly = document.getElementById("changedOnly").checked;
  const palette = theme();

  state.priceField = field;
  state.hoverDate = null;
  state.hoverPricingId = null;
  hideNodeTooltip();

  // The filter only knows about the field being plotted, while the Price changes
  // tab reports every field. Name the field so a model listed there but absent
  // here (it moved a price we aren't plotting) doesn't read as a missing series.
  document.getElementById("changedOnlyLabel").textContent =
    `Only changed ${field.replace(/_/g, " ")}s`;

  let rows = state.series.filter(r =>
    providerVisible("trend", r.provider_id)
    && state.selectedModalities.has(effectiveModality(r.modality))
    && !isFutureSchedulePlaceholder(r),
  );
  if (activeOnly) {
    // A model whose own name says "retired" is not active, whatever the upstream
    // `is_active` flag says. Anthropic keeps retired models listed at full price
    // with a "( retired, except on Bedrock and Google Cloud )" suffix, so the
    // flag stays true while the model is gone for almost everyone.
    const activeIds = new Set(
      state.models
        .filter(m => m.currently_active && !m.name_marks_deprecation)
        .map(m => m.pricing_id),
    );
    rows = rows.filter(r => activeIds.has(r.pricing_id));
  }
  if (changedOnly) {
    const changedIds = pricingIdsThatChanged(rows, field);
    rows = rows.filter(r => changedIds.has(r.pricing_id));
  }

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
      borderWidth: 0.5,
      pointRadius: points.length === 1 ? 3 : 1.5,
      pointHoverRadius: 5,
      pointHitRadius: 10,
      tension: 0,
      spanGaps: true,
      providerId: row.provider_id,
      pricingId: pid,
    });
  }

  datasets.sort((a, b) => (b.data.at(-1)?.y ?? 0) - (a.data.at(-1)?.y ?? 0));
  pruneLegendMode(new Set(datasets.map((ds) => ds.pricingId)));

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
          time: { unit: "day" },
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

  state.defaultHoverDate =
    datasets.reduce((max, ds) => {
      const last = pointDate(ds.data.at(-1));
      return last && last > max ? last : max;
    }, "") || null;
  state.hoverDate = state.defaultHoverDate;
  state.hoverPricingId = null;

  applyLegendVisibility(state.chart);
  state.chart.update();
  renderLegend(state.chart);
}

// ---- archive ---------------------------------------------------------------

// The day the model stopped being current: its own deprecation flag when upstream
// set one, otherwise the last snapshot it appeared in before dropping out of the
// catalog. A row still in the latest snapshot with no flag has no end yet.
function retiredOn(m) {
  return m.deprecated_on || (m.currently_present ? null : m.last_seen);
}

function daysActive(m) {
  const end = retiredOn(m);
  if (!m.first_seen || !end) return null;
  const ms = Date.parse(`${end}T00:00:00Z`) - Date.parse(`${m.first_seen}T00:00:00Z`);
  if (Number.isNaN(ms)) return null;
  return Math.max(0, Math.round(ms / 86400000));
}

function fmtDaysActive(m) {
  const days = daysActive(m);
  if (days == null) return "—";
  // `first_seen` is when the scrape first saw the model, not when the provider
  // shipped it, so anything already listed in the earliest snapshot is a floor.
  const startsBeforeWindow = m.first_seen === state.index?.first_date;
  // Already flagged in the snapshot that introduced it: never observed active, and
  // whatever run it had predates the window entirely. "≥ 0" would assert nothing.
  if (startsBeforeWindow && days === 0) return "—";
  return startsBeforeWindow ? `≥ ${days}` : String(days);
}

function renderArchive() {
  const filter = document.getElementById("archiveFilter").value;
  let rows = state.models.slice();
  const showFlaggedOn = filter === "deprecated";

  if (filter === "deprecated") {
    rows = rows.filter(m =>
      m.deprecated_on || m.name_marks_deprecation || (m.currently_present && !m.currently_active),
    );
  } else {
    rows = rows.filter(m => m.deprecated_on || !m.currently_present || m.name_marks_deprecation);
  }
  rows = rows.filter(m => providerVisible("archive", m.provider_id));

  for (const cell of document.querySelectorAll(".archive-col-flagged-on")) {
    cell.hidden = !showFlaggedOn;
  }

  rows.sort((a, b) => {
    const aKey = a.deprecated_on || a.disappeared_after || "";
    const bKey = b.deprecated_on || b.disappeared_after || "";
    return bKey.localeCompare(aKey);
  });

  // Flagged view has one extra column ("Flagged on"); the all-history view hides it.
  const colCount = showFlaggedOn ? 7 : 6;
  const tbody = document.getElementById("archiveBody");
  tbody.innerHTML = rows.map(m => `
    <tr class="cofair-table__row">
      <td class="cofair-table__td archive-col-name">${esc(m.display_name)}</td>
      <td class="cofair-table__td">${esc(fmtDate(m.first_seen))}</td>
      <td class="cofair-table__td archive-col-flagged-on"${showFlaggedOn ? "" : " hidden"}>${esc(fmtDate(m.deprecated_on))}</td>
      <td class="cofair-table__td">${esc(fmtDate(m.currently_present ? null : m.last_seen))}</td>
      <td class="cofair-table__td cofair-table__td--num">${esc(fmtDaysActive(m))}</td>
      <td class="cofair-table__td cofair-table__td--num">${m.latest_input != null ? `$${esc(m.latest_input)}` : "—"}</td>
      <td class="cofair-table__td cofair-table__td--num">${m.latest_output != null ? `$${esc(m.latest_output)}` : "—"}</td>
    </tr>
  `).join("") || emptyRow(colCount, "No matching models", "Try a different filter or provider.");
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
  const events = detectChanges().filter(e => providerVisible("changes", e.provider_id));
  const tbody = document.getElementById("changesBody");
  if (!events.length) {
    tbody.innerHTML = emptyRow(
      6,
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
        <td class="cofair-table__td">${esc(fmtDate(e.date))}</td>
        <td class="cofair-table__td">${esc(e.display_name)}</td>
        <td class="cofair-table__td">${esc(e.field.replace(/_/g, " "))}</td>
        <td class="cofair-table__td cofair-table__td--num">$${esc(e.from)}</td>
        <td class="cofair-table__td cofair-table__td--num">$${esc(e.to)}</td>
        <td class="cofair-table__td cofair-table__td--num ${dir}">${sign}${e.delta.toFixed(4)} (${sign}${pct}%)</td>
      </tr>
    `;
  }).join("");
}

// ---- token equivalence -----------------------------------------------------

function renderEquivalence() {
  const eq = state.equivalence;
  const modeNode = document.getElementById("eqTierMode");
  if (!eq || !modeNode) return;

  const mode = modeNode.value;
  const pack = document.getElementById("eqPack").value;
  const cadence = document.getElementById("eqCadence").value;
  const packDef = eq.task_packs[pack] || [];

  const modeModels = eq.selected_models_by_mode[mode] || [];
  const budgetRow = eq.budget?.[mode]?.[pack];
  const annual = budgetRow?.annual_usd_by_cadence?.[cadence] ?? null;
  const perRun = budgetRow?.per_run_usd ?? null;

  document.getElementById("eqPerRun").textContent = fmtUsd(perRun);
  document.getElementById("eqAnnual").textContent = fmtUsd(annual);
  document.getElementById("eqMonthly").textContent = fmtUsd(annual == null ? null : annual / 12);
  document.getElementById("eqSafety").textContent = fmtUsd(annual == null ? null : annual * 3);

  const eqSummary = document.getElementById("eqSummary");
  const live = eq.live_runs || {};
  eqSummary.textContent = `${modeModels.length} models · ${packDef.join("+")} · ${cadence} cadence · tokenizer ledger cadence ${eq.tokenizer_ledger.cadence} · latest live day ${live.latest_date || "—"}`;

  const modelBody = document.getElementById("eqModelBody");
  if (!modeModels.length) {
    modelBody.innerHTML = emptyRow(5, "No sentinel models selected", "Selected provider tiers are missing priced active models.");
  } else {
    modelBody.innerHTML = modeModels.map((row) => `
      <tr class="cofair-table__row">
        <td class="cofair-table__td">${providerBadge(row.provider_id)}</td>
        <td class="cofair-table__td">${esc(row.tier)}</td>
        <td class="cofair-table__td">${esc(row.display_name)}</td>
        <td class="cofair-table__td cofair-table__td--num">${fmtUsd(row.input_price)}</td>
        <td class="cofair-table__td cofair-table__td--num">${fmtUsd(row.output_price)}</td>
      </tr>
    `).join("");
  }

  const taskMap = new Map(eq.tasks.map((task) => [task.task_id, task]));
  const taskBody = document.getElementById("eqTaskBody");
  const taskRows = packDef
    .map((taskId) => taskMap.get(taskId))
    .filter(Boolean);
  taskBody.innerHTML = taskRows.map((task) => `
    <tr class="cofair-table__row">
      <td class="cofair-table__td">${esc(task.task_id)} · ${esc(task.label)}</td>
      <td class="cofair-table__td cofair-table__td--num">${esc(task.input_tokens.toLocaleString())}</td>
      <td class="cofair-table__td cofair-table__td--num">${esc(task.output_tokens.toLocaleString())}</td>
      <td class="cofair-table__td">${esc(task.cadence)}</td>
    </tr>
  `).join("") || emptyRow(4, "No tasks in this pack", "Choose another pack.");

  const budgetBody = document.getElementById("eqBudgetBody");
  budgetBody.innerHTML = Object.entries(eq.task_packs).map(([packId, taskIds]) => {
    const budget = eq.budget?.[mode]?.[packId];
    return `
      <tr class="cofair-table__row">
        <td class="cofair-table__td">${esc(packId)} (${esc(taskIds.join("+"))})</td>
        <td class="cofair-table__td cofair-table__td--num">${fmtUsd(budget?.annual_usd_by_cadence?.daily ?? null)}</td>
        <td class="cofair-table__td cofair-table__td--num">${fmtUsd(budget?.annual_usd_by_cadence?.weekly ?? null)}</td>
        <td class="cofair-table__td cofair-table__td--num">${fmtUsd(budget?.annual_usd_by_cadence?.biweekly ?? null)}</td>
        <td class="cofair-table__td cofair-table__td--num">${fmtUsd(budget?.annual_usd_by_cadence?.monthly ?? null)}</td>
      </tr>
    `;
  }).join("");

  document.getElementById("eqTokenizerStatus").textContent = eq.tokenizer_ledger.status.replace(/_/g, " ");
  document.getElementById("eqTokenizerNote").textContent = eq.tokenizer_ledger.note;

  const liveBody = document.getElementById("eqLiveBody");
  const latestRows = live.latest_rows || [];
  if (!latestRows.length) {
    liveBody.innerHTML = emptyRow(4, "No live weekly runs yet", "Run scripts/run_equivalence_tasks.py to publish observed token usage.");
  } else {
    const okCount = latestRows.filter((row) => row.run_status === "ok").length;
    const statusCounts = new Map();
    for (const row of latestRows) {
      statusCounts.set(row.run_status, (statusCounts.get(row.run_status) || 0) + 1);
    }
    const statusSummary = [...statusCounts.entries()]
      .map(([status, count]) => `${status}: ${count}`)
      .join(", ");
    liveBody.innerHTML = `
      <tr class="cofair-table__row">
        <td class="cofair-table__td">${esc(live.latest_date)}</td>
        <td class="cofair-table__td cofair-table__td--num">${esc(String(latestRows.length))}</td>
        <td class="cofair-table__td cofair-table__td--num">${esc(String(okCount))}</td>
        <td class="cofair-table__td">${esc(statusSummary)}</td>
      </tr>
    `;
  }

  const authBody = document.getElementById("eqAuthBody");
  const authReqs = eq.provider_auth?.requirements || {};
  const providers = Object.keys(authReqs).sort();
  authBody.innerHTML = providers.map((provider) => {
    const row = authReqs[provider];
    const configured = row.configured ? "yes" : "no";
    const envDisplay = Array.isArray(row.env) ? row.env.join(" | ") : row.env;
    return `
      <tr class="cofair-table__row">
        <td class="cofair-table__td">${providerBadge(provider)}</td>
        <td class="cofair-table__td"><code>${esc(envDisplay)}</code></td>
        <td class="cofair-table__td">${configured}</td>
        <td class="cofair-table__td">${esc(row.purpose)}</td>
      </tr>
    `;
  }).join("") || emptyRow(4, "No auth requirements recorded", "Rebuild dashboard artifacts.");
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
    if (tab.dataset.tab === "equivalence") renderEquivalence();
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
  ["priceField", "yScale", "activeOnly", "changedOnly"].forEach(id => {
    document.getElementById(id).addEventListener("change", renderTrendChart);
  });
  ["archiveFilter"].forEach(id => {
    document.getElementById(id).addEventListener("change", renderArchive);
  });
  ["eqTierMode", "eqPack", "eqCadence"].forEach((id) => {
    const node = document.getElementById(id);
    if (!node) return;
    node.addEventListener("change", renderEquivalence);
  });
}

// ---- init ------------------------------------------------------------------

async function main() {
  try {
    await loadData();
  } catch (e) {
    const rangeSummary = document.getElementById("rangeSummary");
    if (rangeSummary) {
      rangeSummary.textContent =
        "Failed to load data/*.json. Did you run scripts/build_dashboard_data.py --rebuild?";
    }
    console.error(e);
    return;
  }
  renderHeader();
  renderProviderChips();
  renderModalityChips();
  renderArchiveProviderChips();
  renderChangesProviderChips();
  setupTabs();
  bindControls();
  document.getElementById("trendChart").addEventListener("mouseleave", clearChartHover);
  renderTrendChart();
  renderArchive();
  renderChanges();
}

main();
