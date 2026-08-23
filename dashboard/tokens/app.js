// Token equivalence index — daily token-consumption drift for a frozen task
// corpus. Reads dashboard/data/equivalence.json (see build_dashboard_data.py).
//
// Provider tinting matches /pricing exactly; every color comes from a
// --cofair-* token, nothing visual is hard-coded here (hub R13).

import { hexToHsl, priceAtOrBefore, sortDatasetsForDate } from "../labels.js";

const Chart = window.Chart;

const TIERS = ["flagship", "workhorse"];

const state = {
  eq: null,
  // provider_id → "solo" | "hidden". Absent means "shown alongside the rest",
  // which is the resting state for every provider, so an untouched map means
  // everything is visible. Mirrors /pricing's legend interaction.
  providerMode: new Map(),
  // Tiers left showing. A plain include/exclude set like /pricing's Modality
  // filter — unlike providers, there is no solo position to cycle through.
  tiers: new Set(TIERS),
  pack: "suiteLong",
  metric: "tokens_in_per_1k_chars",
  // Ledger tab filters. `ledgerTask` is "" for every task; the dates are
  // ISO days clamped to the observed ledger window (see ledgerDates()).
  ledgerTask: "",
  ledgerFrom: "",
  ledgerTo: "",
  // Chart rollover, mirroring /pricing: the date column under the cursor and
  // the provider|tier line whose node is being pointed at (null off a node).
  // Resting state is the most recent scraped day rather than nothing, so the
  // legend opens on a real reading instead of an unlabelled column.
  hoverDate: null,
  hoverLineKey: null,
  defaultHoverDate: null,
  chart: null,
  lastFocus: null,
};

const SERIES_COUNT = 7;

// Corpus entry E is counted, never generated, so it is absent from every meter
// and ledger row and is the only task the wrapper measure can report.
const CHAT_TASK_ID = "E";
const DEFAULT_METER_TASK_IDS = ["A", "B", "C", "D"];

// Same provider → series map as the pricing dashboard (../styles.css overrides 2–7).
const PREFERRED_PROVIDER_SERIES = new Map([
  ["openai", 0],
  ["anthropic", 1],
  ["google", 2],
  ["aws", 3],
  ["deepseek", 4],
  ["qwen", 5],
  ["xai", 6],
]);

// Display-only relabel: snapshot provider_id "aws" stays for contract
// continuity; surface shows lowercase "amazon".
const PROVIDER_LABELS = new Map([["aws", "amazon"]]);

const METRICS = {
  tokens_in_per_1k_chars: {
    label: "Input tokens per 1,000 characters, pack-weighted (meter)",
    axis: "tokens / 1K chars (pack-weighted)",
    source: "meter",
    note:
      "Tokenizer density from the daily task meter, pooled across the pack: total input " +
      "tokens over total characters. Task D carries 25,743 of the suite's ~27,000 " +
      "characters, so it dominates this figure. A step change on a pinned model is the " +
      "signature of a silent re-tokenization.",
    decimals: 1,
  },
  ledger_density: {
    label: "Input tokens per 1,000 characters, mean of tasks A–C (ledger)",
    axis: "tokens / 1K chars (task mean)",
    source: "ledger",
    note:
      "Count-only daily ledger, no generation: the unweighted mean of the per-task " +
      "densities for A, B and C. Every task counts equally here, so short prompts carry " +
      "the same weight as long ones and the value runs several times above the " +
      "pack-weighted meter. The two are different statistics on the same corpus — read " +
      "each against its own history, not against the other.",
    decimals: 1,
  },
  wrapper_turn10: {
    label: "Turn-10 prompt tokens (Test 4)",
    axis: "prompt tokens",
    source: "wrapper",
    note:
      "Frozen 10-turn transcript counted at turn 10. Moves when providers change how they " +
      "pack chat history / inject wrappers — not when the transcript text changes.",
    decimals: 0,
  },
  tokens_total: {
    label: "Total tokens consumed",
    axis: "tokens",
    source: "meter",
    note:
      "Input + output tokens billed for the selected pack. Moves with tokenizer density AND " +
      "model verbosity, so treat it as cost telemetry rather than drift evidence.",
    decimals: 0,
  },
  tokens_in: {
    label: "Input tokens",
    axis: "tokens",
    source: "meter",
    note:
      "Deterministic for a fixed prompt and pinned model — the cleanest drift signal, " +
      "though not comparable across providers until normalized by characters.",
    decimals: 0,
  },
  tokens_out: {
    label: "Output tokens",
    axis: "tokens",
    source: "meter",
    note:
      "Stochastic, and now bounded only by a generous ceiling rather than a per-task cap — " +
      "so a model that grows more verbose between versions shows up here instead of flattening " +
      "against the cap. Runs that still reach the ceiling are censored observations.",
    decimals: 0,
  },
  usd: {
    label: "Cost per run",
    axis: "USD",
    source: "meter",
    note:
      "Observed tokens valued at the same-day rate card, with output allowed to run to its " +
      "natural length — this is what the work actually costs, not what a truncated answer cost. " +
      "Separates price changes from consumption changes only when read alongside tokenizer density.",
    decimals: 4,
  },
};

const MONEY_FMT = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 4,
});

const DATE_FMT = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "short",
  day: "numeric",
  timeZone: "UTC",
});

function esc(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function providerLabel(providerId) {
  return PROVIDER_LABELS.get(providerId) || providerId;
}

function providerSeries(providerId) {
  if (PREFERRED_PROVIDER_SERIES.has(providerId)) {
    return (PREFERRED_PROVIDER_SERIES.get(providerId) % SERIES_COUNT) + 1;
  }
  let hash = 0;
  for (const ch of providerId) hash = (hash + ch.charCodeAt(0)) % SERIES_COUNT;
  return hash + 1;
}

function providerBadge(providerId) {
  return `<span class="cofair-badge" data-series="${providerSeries(providerId)}">${esc(
    providerLabel(providerId),
  )}</span>`;
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

function trendsPanelVisible() {
  const panel = document.getElementById("panel-trends");
  return Boolean(panel) && !panel.hidden;
}

/** Read live token values so the chart follows the active theme. */
function themeColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**
 * Chart-facing snapshot of the design tokens. Chart.js needs literal colors and
 * pixel numbers, so every value is resolved from a --cofair-* custom property
 * at draw time rather than hard-coded (hub R13). Mirrors /pricing's theme().
 */
function chartPalette() {
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
    radiusSm: px("--cofair-radius-sm"),
    padding: px("--cofair-space-2"),
  };
}

function providerColor(providerId) {
  return themeColor(`--cofair-dataviz-${providerSeries(providerId)}`) || themeColor("--cofair-color-text-muted");
}

// A provider's two tiers share its hue, so the dash pattern was doing all the
// work of telling them apart — and at 0.5px it loses that argument. Lifting the
// workhorse lightness gives the pair a second, coarser difference.
const WORKHORSE_LIGHTEN = 18;

/**
 * The line color for one provider-tier: the provider's data-viz step, brightened
 * for workhorse so the two tiers separate on value as well as on dash pattern.
 */
function tierColor(providerId, tier) {
  const base = providerColor(providerId);
  if (tier === "flagship" || !base.startsWith("#")) return base;
  const { h, s, l } = hexToHsl(base);
  if (Number.isNaN(h)) return base;
  const lifted = Math.min(78, l + WORKHORSE_LIGHTEN);
  return `hsl(${h.toFixed(1)}, ${s.toFixed(1)}%, ${lifted.toFixed(1)}%)`;
}

function fmtMetric(value, metric) {
  if (value == null || Number.isNaN(value)) return "—";
  if (metric === "usd") return MONEY_FMT.format(value);
  const decimals = METRICS[metric].decimals;
  return value.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function fmtDate(value) {
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) ? value : DATE_FMT.format(parsed);
}

function packTaskIds() {
  return state.eq?.task_packs?.[state.pack] || [];
}

/** Pack members that a meter or ledger run actually generates. */
function meterTaskIds() {
  const generating = new Set(state.eq?.meter_task_ids || DEFAULT_METER_TASK_IDS);
  return packTaskIds().filter((id) => generating.has(id));
}

function packHasChatTask() {
  return packTaskIds().includes(CHAT_TASK_ID);
}

function packTasks() {
  const byId = new Map((state.eq?.tasks || []).map((task) => [task.task_id, task]));
  return meterTaskIds().map((id) => byId.get(id)).filter(Boolean);
}

// ---- provider visibility ---------------------------------------------------

function soloProvider() {
  for (const [providerId, mode] of state.providerMode) {
    if (mode === "solo") return providerId;
  }
  return null;
}

function providerVisible(providerId) {
  const solo = soloProvider();
  if (solo) return providerId === solo;
  return state.providerMode.get(providerId) !== "hidden";
}

function tierVisible(tier) {
  return state.tiers.has(tier);
}

/** Both surfaces filter on the same two axes; keep the test in one place. */
function panelVisible(providerId, tier) {
  return providerVisible(providerId) && tierVisible(tier);
}

/**
 * none → solo → hidden → none, with at most one provider soloed at a time.
 *
 * While a provider is isolated every other chip reads as "not shown", so a
 * click on one of them means "isolate this one instead" — whatever mode that
 * chip happened to be left in before the isolation took effect.
 */
function cycleProviderMode(providerId) {
  const solo = soloProvider();
  if (solo && solo !== providerId) {
    state.providerMode.delete(solo);
    state.providerMode.set(providerId, "solo");
    return;
  }
  const mode = state.providerMode.get(providerId);
  if (!mode) {
    state.providerMode.set(providerId, "solo");
    return;
  }
  if (mode === "solo") {
    state.providerMode.set(providerId, "hidden");
    return;
  }
  state.providerMode.delete(providerId);
}

/**
 * Aggregate observed rows into one point per (date, provider, tier).
 *
 * Meter: require every task in the pack; density recomputed from summed tokens.
 * Ledger: average density across selected pack tasks for that day.
 * Wrapper: turn-10 prompt tokens only.
 */
function aggregate() {
  const metric = METRICS[state.metric];
  const source = metric.source || "meter";

  if (source === "ledger") return aggregateLedger();
  if (source === "wrapper") return aggregateWrapper();
  return aggregateMeter();
}

function aggregateMeter() {
  const taskIds = meterTaskIds();
  const wanted = new Set(taskIds);
  const groups = new Map();
  if (!taskIds.length) return { points: [], incomplete: 0, xField: "date" };

  for (const row of state.eq?.token_runs || []) {
    if (!wanted.has(row.task_id)) continue;
    if (!panelVisible(row.provider_id, row.tier)) continue;

    const key = `${row.date}|${row.provider_id}|${row.tier}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        date: row.date,
        provider_id: row.provider_id,
        tier: row.tier,
        model_id: row.model_id,
        api_model: row.api_model,
        tasks: new Set(),
        tokens_in: 0,
        tokens_out: 0,
        tokens_total: 0,
        input_chars: 0,
        usd: 0,
        censored: false,
        replicate_count: row.replicate_count || 1,
      };
      groups.set(key, group);
    }
    group.tasks.add(row.task_id);
    group.tokens_in += row.tokens_in;
    group.tokens_out += row.tokens_out;
    group.tokens_total += row.tokens_total;
    group.input_chars += row.input_chars;
    group.usd += row.usd || 0;
    if (row.output_censored) group.censored = true;
    group.replicate_count = Math.max(group.replicate_count, row.replicate_count || 1);
  }

  const complete = [];
  let incomplete = 0;
  for (const group of groups.values()) {
    if (group.tasks.size !== taskIds.length) {
      incomplete += 1;
      continue;
    }
    group.tokens_in_per_1k_chars = group.tokens_in / (group.input_chars / 1000);
    complete.push(group);
  }
  complete.sort((a, b) => a.date.localeCompare(b.date));
  return { points: complete, incomplete, xField: "date" };
}

function aggregateLedger() {
  // Average over a FIXED task set, never "whatever was collected that day".
  // D stays out of the mean even though it is now collected daily: it is the
  // long-context task and runs ~20% below the prose and code tasks, so folding
  // it in would move the level of the line without telling you anything the
  // `long` pack does not show on its own.
  const generating = meterTaskIds();
  const required = generating.filter((id) => id !== "D");
  const need = required.length ? required : generating;
  const taskIds = new Set(need);
  if (!need.length) return { points: [], incomplete: 0, xField: "date" };

  const groups = new Map();
  for (const row of state.eq?.tokenizer_ledger?.rows || []) {
    if (!taskIds.has(row.task_id)) continue;
    if (!panelVisible(row.provider_id, row.tier)) continue;
    if (row.tokens_in_per_1k_chars == null) continue;

    const key = `${row.date}|${row.provider_id}|${row.tier}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        date: row.date,
        provider_id: row.provider_id,
        tier: row.tier,
        model_id: row.model_id,
        api_model: row.api_model,
        tasks: new Set(),
        densitySum: 0,
        n: 0,
      };
      groups.set(key, group);
    }
    group.tasks.add(row.task_id);
    group.densitySum += row.tokens_in_per_1k_chars;
    group.n += 1;
  }

  const complete = [];
  let incomplete = 0;
  for (const group of groups.values()) {
    if (need.some((id) => !group.tasks.has(id))) {
      incomplete += 1;
      continue;
    }
    group.ledger_density = group.densitySum / group.n;
    complete.push(group);
  }
  complete.sort((a, b) => a.date.localeCompare(b.date));
  return { points: complete, incomplete, xField: "date" };
}

function aggregateWrapper() {
  const groups = new Map();
  if (!packHasChatTask()) return { points: [], incomplete: 0, xField: "date" };
  for (const row of state.eq?.wrapper_runs?.rows || []) {
    if (row.turn !== 10) continue;
    if (!panelVisible(row.provider_id, row.tier)) continue;
    if (row.api_prompt_tokens == null) continue;
    const key = `${row.run_date}|${row.provider_id}|${row.tier}`;
    groups.set(key, {
      date: row.run_date,
      provider_id: row.provider_id,
      tier: row.tier,
      model_id: row.model_id,
      api_model: row.api_model,
      wrapper_turn10: row.api_prompt_tokens,
    });
  }
  const complete = [...groups.values()].sort((a, b) => a.date.localeCompare(b.date));
  return { points: complete, incomplete: 0, xField: "date" };
}

/**
 * Panel rows that collected nothing from the source behind the current measure.
 *
 * Without this, a provider whose every call failed draws exactly like a
 * provider that is simply absent from the chart — which is how this surface sat
 * dark for weeks while the pipeline reported success. Read the collection
 * health the builder derives from observed rows, not the env-key check.
 */
function darkPanelRows() {
  const source = METRICS[state.metric].source || "meter";
  return (state.eq?.provider_health?.panel || []).filter((item) => {
    const stats = item.sources?.[source];
    return stats && !stats.ok_count && stats.error_count;
  });
}

function fillProviderChips(elementId) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const providers = Object.keys(state.eq.provider_auth?.requirements || {}).sort();
  el.innerHTML = "";
  providers.forEach((provider) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip cofair-badge";
    chip.dataset.series = String(providerSeries(provider));
    chip.dataset.provider = provider;
    chip.innerHTML = `<span class="chip__swatch"></span>${esc(providerLabel(provider))}`;
    chip.addEventListener("click", () => {
      cycleProviderMode(provider);
      render();
    });
    el.appendChild(chip);
  });
}

/**
 * Tier chips, on the same include/exclude model as /pricing's Modality filter.
 *
 * Both panels get a row, and both rows drive the one `state.tiers` set, so the
 * chart and the ledger can never disagree about which tiers are in view.
 */
function fillTierChips(elementId) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.innerHTML = "";
  for (const tier of TIERS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip cofair-badge";
    chip.dataset.tier = tier;
    chip.textContent = tier.charAt(0).toUpperCase() + tier.slice(1);
    chip.addEventListener("click", () => {
      // Never let the last tier out: an empty set reads as a broken chart
      // rather than as a filter the reader applied.
      if (state.tiers.has(tier)) {
        if (state.tiers.size === 1) return;
        state.tiers.delete(tier);
      } else {
        state.tiers.add(tier);
      }
      render();
    });
    el.appendChild(chip);
  }
}

function syncTierChips() {
  document.querySelectorAll(".chip[data-tier]").forEach((chip) => {
    const tier = chip.dataset.tier;
    const on = state.tiers.has(tier);
    chip.setAttribute("aria-pressed", String(on));
    chip.dataset.mode = on ? "" : "hidden";
    // State never rests on styling alone: say which position the chip is in and
    // whether clicking it will do anything (the last tier on cannot be removed).
    const last = on && state.tiers.size === 1;
    chip.setAttribute(
      "aria-label",
      `${tier}: ${on ? (last ? "shown — the only tier left" : "shown — click to hide") : "hidden — click to show"}`,
    );
    // aria-disabled, not `disabled`: the chip stays focusable so a keyboard
    // reader can still find it and read why it will not respond.
    chip.setAttribute("aria-disabled", String(last));
  });
}

function renderProviderChips() {
  fillProviderChips("providerFilter");
  fillProviderChips("ledgerProviderFilter");
  fillTierChips("tierFilter");
  fillTierChips("ledgerTierFilter");
  syncProviderChips();
  syncTierChips();
}

/**
 * Push the visibility model onto the chips. Kept apart from construction because
 * soloing one provider changes how every *other* chip reads, so a click cannot
 * just update the chip that was clicked.
 */
function syncProviderChips() {
  const solo = soloProvider();
  document.querySelectorAll(".chip[data-provider]").forEach((chip) => {
    const provider = chip.dataset.provider;
    const mode = state.providerMode.get(provider) || "";
    chip.dataset.mode = mode;
    chip.setAttribute("aria-pressed", String(providerVisible(provider)));
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
    chip.setAttribute("aria-label", `${providerLabel(provider)}: ${position}`);
  });
}

function renderHealth() {
  const dark = darkPanelRows();
  const byProvider = new Map();
  dark.forEach((item) => {
    if (!byProvider.has(item.provider_id)) byProvider.set(item.provider_id, []);
    byProvider.get(item.provider_id).push(item);
  });

  document.querySelectorAll(".chip[data-provider]").forEach((chip) => {
    const rows = byProvider.get(chip.dataset.provider);
    if (!rows) {
      delete chip.dataset.dark;
      chip.removeAttribute("title");
      return;
    }
    chip.dataset.dark = "true";
    chip.title = rows
      .map((row) => {
        const stats = row.sources[METRICS[state.metric].source || "meter"];
        return `${row.tier}: ${stats.last_error_model || "?"} — ${String(stats.last_error || "").slice(0, 140)}`;
      })
      .join("\n");
  });

  const note = document.getElementById("healthNote");
  note.hidden = dark.length === 0;
  note.textContent = dark.length
    ? `Not collecting for this measure: ${dark
        .map((item) => `${providerLabel(item.provider_id)} · ${item.tier}`)
        .join(", ")}. The last attempt returned an error, so these are missing rather than flat.`
    : "";
}

/**
 * Why the chart is empty, in the reader's terms.
 *
 * A pack/measure pair can be legitimately empty — task E has no generated
 * output, tasks A–D have no chat wrapper — and "no runs yet" would read as a
 * broken pipeline instead of a combination that cannot have data by design.
 */
function emptyChartReason() {
  const source = METRICS[state.metric].source || "meter";
  if (source === "wrapper" && !packHasChatTask()) {
    return "Wrapper overhead is measured on task E only. Choose the full suite or E · Chat transcript.";
  }
  if (source !== "wrapper" && !meterTaskIds().length) {
    return "Task E is counted, never generated, so it has no tokens of its own. Choose the full suite or a task A–D.";
  }
  if (!state.providerMode.size) return "No completed runs yet.";
  return "Every provider is hidden. Click a provider pill to bring it back.";
}

function pointDate(point) {
  if (!point) return null;
  if (typeof point.x === "string") return point.x.slice(0, 10);
  const d = new Date(point.x);
  return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
}

function hideNodeTooltip() {
  const tooltip = document.getElementById("chartNodeTooltip");
  if (tooltip) tooltip.hidden = true;
}

/**
 * The rollover card /pricing shows on a node, carrying this chart's reading.
 *
 * Same three lines in the same order so the two trend charts read alike:
 * provider, the series identity (tier here, model on /pricing), then the date
 * and the measured value.
 */
function showNodeTooltip(chart, hit) {
  const tooltip = document.getElementById("chartNodeTooltip");
  if (!tooltip || !hit) return;

  const ds = chart.data.datasets[hit.datasetIndex];
  const point = ds.data[hit.index];
  const element = hit.element;
  const canvas = chart.canvas;
  const plot = canvas.parentElement;
  if (!point || !plot || element?.x == null || element?.y == null) return;

  const provider = tooltip.querySelector(".chart-node-tooltip__provider");
  const model = tooltip.querySelector(".chart-node-tooltip__model");
  const meta = tooltip.querySelector(".chart-node-tooltip__meta");
  if (!provider || !model || !meta) return;

  provider.textContent = `${providerLabel(ds.providerId)} · ${ds.tier}`;
  model.textContent = ds.model || "—";
  meta.textContent = `${fmtDate(pointDate(point))} · ${fmtMetric(point.y, state.metric)}`;

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
  if (state.hoverDate === state.defaultHoverDate && !state.hoverLineKey) return;
  state.hoverDate = state.defaultHoverDate;
  state.hoverLineKey = null;
  if (state.chart) {
    renderChartLegend(state.chart);
    state.chart.draw();
  }
}

const dateMarkerPlugin = {
  id: "dateMarker",
  afterDraw(chart) {
    if (!state.hoverDate) return;
    const xScale = chart.scales.x;
    if (!xScale) return;
    const x = xScale.getPixelForValue(`${state.hoverDate}T00:00:00Z`);
    const { top, bottom, left, right } = chart.chartArea;
    if (x < left || x > right) return;
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = chartPalette().muted;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.restore();
  },
};

function renderChartLegend(chart) {
  const dateEl = document.getElementById("chartLegendDate");
  const unitEl = document.getElementById("chartLegendUnit");
  const list = document.getElementById("chartLegend");
  if (!list) return;

  const datasets = chart?.data?.datasets || [];
  if (dateEl) dateEl.textContent = state.hoverDate ? fmtDate(state.hoverDate) : "Provider · tier";
  // The numeric column carries no unit of its own, so the header names the one
  // the y axis is drawn in — otherwise "3.9" and "4,182" read as the same kind.
  if (unitEl) unitEl.textContent = datasets.length ? METRICS[state.metric].axis : "";

  if (!datasets.length) {
    list.innerHTML = `<li class="cofair-text cofair-text--xs">${esc(emptyChartReason())}</li>`;
    return;
  }

  // Rank by the reading on the selected date so the legend order matches what
  // the eye sees in that column.
  const ordered = state.hoverDate
    ? sortDatasetsForDate(datasets, state.hoverDate)
    : [...datasets].sort((a, b) => (b.data.at(-1)?.y ?? 0) - (a.data.at(-1)?.y ?? 0));

  list.innerHTML = ordered
    .map((ds) => {
      const value = state.hoverDate
        ? priceAtOrBefore(ds.data, state.hoverDate)
        : ds.data.at(-1)?.y;
      const active = state.hoverLineKey === ds.lineKey ? " chart-legend__item--active" : "";
      return `<li class="chart-legend__item${active}">
        <span class="legend-swatch legend-swatch--${ds.tier === "flagship" ? "solid" : "dashed"}"
              style="--legend-color: ${esc(ds.borderColor)}"></span>
        <span class="legend-id">
          <span class="cofair-text cofair-text--xs">${esc(providerLabel(ds.providerId))} · ${esc(ds.tier)}</span>
          <span class="cofair-text cofair-text--xs legend-model">${esc(ds.model || "—")}</span>
        </span>
        <span class="cofair-text cofair-text--xs legend-value">${esc(fmtMetric(value, state.metric))}</span>
      </li>`;
    })
    .join("");
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
  const onNode = chart.getElementsAtEventForMode(evt, "nearest", { intersect: true }, false);

  const date = alongX[0]
    ? pointDate(chart.data.datasets[alongX[0].datasetIndex].data[alongX[0].index])
    : null;
  const lineKey = onNode[0] ? chart.data.datasets[onNode[0].datasetIndex].lineKey : null;

  if (onNode[0]) showNodeTooltip(chart, onNode[0]);
  else hideNodeTooltip();

  if (date === state.hoverDate && lineKey === state.hoverLineKey) return;
  state.hoverDate = date;
  state.hoverLineKey = lineKey;
  renderChartLegend(chart);
  chart.draw();
}

function renderChart(points) {
  const canvas = document.getElementById("tokenChart");
  if (!Chart) return;

  // Rest on the most recent scraped day rather than on nothing, so the legend
  // opens showing that day's readings. No series is selected — the highlight is
  // reserved for an actual rollover.
  const latest = points.reduce((max, p) => (p.date > max ? p.date : max), "");
  state.defaultHoverDate = latest || null;
  state.hoverDate = state.defaultHoverDate;
  state.hoverLineKey = null;
  hideNodeTooltip();

  const byLine = new Map();
  for (const point of points) {
    const key = `${point.provider_id}|${point.tier}`;
    if (!byLine.has(key)) byLine.set(key, []);
    byLine.get(key).push(point);
  }

  const datasets = [];
  for (const [key, linePoints] of [...byLine.entries()].sort()) {
    const [providerId, tier] = key.split("|");
    const color = tierColor(providerId, tier);
    const dash = tier === "flagship" ? [] : [5, 4];
    const ordered = [...linePoints].sort((a, b) => a.date.localeCompare(b.date));
    datasets.push({
      label: `${providerLabel(providerId)} · ${tier}`,
      data: ordered.map((p) => ({ x: `${p.date}T00:00:00Z`, y: p[state.metric] })),
      borderColor: color,
      backgroundColor: color,
      borderDash: dash,
      // Chart.js resolves hover styling separately, so without this the line a
      // reader is pointing at snaps to solid — losing the tier cue at exactly
      // the moment they are trying to read the tier.
      hoverBorderDash: dash,
      borderWidth: 0.5,
      hoverBorderWidth: 0.5,
      pointStyle: "circle",
      pointBackgroundColor: color,
      pointBorderColor: color,
      pointBorderWidth: 1,
      // Same node and rollover geometry as /pricing's trend chart.
      pointRadius: ordered.length === 1 ? 3 : 1.5,
      pointHoverRadius: 5,
      pointHitRadius: 10,
      tension: 0,
      spanGaps: true,
      providerId,
      tier,
      lineKey: key,
      // The model the panel is pinned to now. Read from the newest observation
      // because a re-pin mid-window makes the oldest one a stale identity.
      model: ordered.at(-1)?.api_model || ordered.at(-1)?.model_id || "",
    });
  }

  const metric = METRICS[state.metric];
  const palette = chartPalette();
  const tickFont = { family: palette.mono, size: palette.sizeXs };
  const labelFont = { family: palette.sans, size: palette.sizeXs };

  // A single observed date collapses the time scale to zero width, which drops
  // every x tick. Pad it to a week so the date the point belongs to stays legible.
  const dates = new Set(points.map((p) => p.date));
  let xBounds = {};
  if (dates.size === 1) {
    const t = new Date(`${[...dates][0]}T00:00:00Z`).getTime();
    const halfWeek = 3.5 * 24 * 60 * 60 * 1000;
    xBounds = { min: t - halfWeek, max: t + halfWeek };
  }

  if (state.chart) state.chart.destroy();
  state.chart = new Chart(canvas, {
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
        // The rollover is the DOM card in .chart-card__plot, as on /pricing.
        tooltip: { enabled: false },
      },
      scales: {
        x: {
          type: "time",
          time: { unit: "day" },
          ...xBounds,
          grid: { color: palette.grid },
          border: { color: palette.grid },
          ticks: { color: palette.muted, font: tickFont },
        },
        y: {
          beginAtZero: state.metric !== "tokens_in_per_1k_chars",
          grid: { color: palette.grid },
          border: { color: palette.grid },
          ticks: { color: palette.muted, font: tickFont },
          title: {
            display: true,
            text: metric.axis,
            color: palette.muted,
            font: labelFont,
          },
        },
      },
    },
  });

  renderChartLegend(state.chart);
  canvas.addEventListener("mouseleave", clearChartHover);
}

/**
 * "What it probes" as a hover/focus tooltip beside the task link.
 *
 * Reuses the design system's `.cofair-tooltip` skin rather than introducing a
 * tooltip of its own (hub R13); only the trigger and positioning are local.
 */
function probeTooltip(taskId, probes) {
  const id = `probes-${taskId}`;
  return `<span class="task-info">
    <button type="button" class="task-info__trigger" aria-describedby="${esc(id)}"
            aria-label="What task ${esc(taskId)} probes">?</button>
    <span class="cofair-tooltip task-info__bubble" role="tooltip" id="${esc(id)}">${esc(probes)}</span>
  </span>`;
}

function taskRowHtml({ taskId, label, probes, inputChars, inputWords, chat }) {
  return `<tr class="cofair-table__row">
    <td class="cofair-table__td">
      <span class="task-cell">
        <button type="button" class="task-link" ${chat ? 'data-chat="1"' : `data-task="${esc(taskId)}"`}>
          ${esc(taskId)} · ${esc(label)}
        </button>
        ${probeTooltip(taskId, probes)}
      </span>
    </td>
    <td class="cofair-table__td cofair-table__td--num">${inputChars.toLocaleString()}</td>
    <td class="cofair-table__td cofair-table__td--num">${inputWords.toLocaleString()}</td>
  </tr>`;
}

/** Corpus entry E — listed only when the selected pack includes it. */
function chatTaskRow() {
  const transcript = state.eq?.chat_transcript || [];
  if (!transcript.length || !packHasChatTask()) return "";
  const chat = state.eq?.chat_task || {};
  const text = transcript.map((turn) => turn.text).join("\n");
  return taskRowHtml({
    taskId: chat.task_id || CHAT_TASK_ID,
    label: chat.label || "Chat transcript",
    probes:
      chat.probes ||
      "Chat wrapper and history-packing overhead across 10 frozen turns.",
    inputChars: chat.input_chars ?? text.length,
    inputWords: chat.input_words ?? text.trim().split(/\s+/).filter(Boolean).length,
    chat: true,
  });
}

function ledgerTaskLabels() {
  const labels = new Map((state.eq?.tasks || []).map((task) => [task.task_id, task.label]));
  const chat = state.eq?.chat_task || {};
  labels.set(chat.task_id || CHAT_TASK_ID, chat.label || "Chat transcript");
  return labels;
}

/**
 * Every ledger observation, provider filter and tab filters not yet applied.
 *
 * Task E is counted on the frozen chat transcript rather than the A–D corpus,
 * so it lives in `wrapper_runs` and is folded in here — the ledger is meant to
 * read as one table of daily counts, not two.
 */
function ledgerAllRows() {
  const ledgerRows = (state.eq?.tokenizer_ledger?.rows || [])
    .filter((row) => row.run_status === "ok")
    .map((row) => ({
      date: row.date,
      provider_id: row.provider_id,
      tier: row.tier,
      model: row.api_model || row.model_id,
      task_id: row.task_id,
      tokens_in: row.tokens_in,
      density: row.tokens_in_per_1k_chars,
    }));

  const chatRows = (state.eq?.wrapper_runs?.rows || [])
    .filter((row) => row.run_status === "ok" && row.turn === 10 && row.api_prompt_tokens != null)
    .map((row) => ({
      date: row.run_date,
      provider_id: row.provider_id,
      tier: row.tier,
      model: row.api_model || row.model_id,
      task_id: CHAT_TASK_ID,
      tokens_in: row.api_prompt_tokens,
      density: row.tokens_per_1k_chars,
    }));

  return [...ledgerRows, ...chatRows].sort((a, b) => {
    const date = (b.date || "").localeCompare(a.date || "");
    if (date) return date;
    const provider = providerLabel(a.provider_id).localeCompare(providerLabel(b.provider_id));
    if (provider) return provider;
    const tier = (a.tier || "").localeCompare(b.tier || "");
    if (tier) return tier;
    return (a.task_id || "").localeCompare(b.task_id || "");
  });
}

/** The observed ledger window — what the date pickers may legitimately offer. */
function ledgerDates() {
  const dates = [...new Set(ledgerAllRows().map((row) => row.date).filter(Boolean))].sort();
  return { first: dates[0] || "", last: dates[dates.length - 1] || "", all: dates };
}

/**
 * Populate the Task select and clamp the date inputs to the observed window.
 *
 * Both are derived from the data rather than hard-coded: the ledger grows a day
 * at a time, and a picker offering days the scraper never ran reads as a gap in
 * the record rather than a gap in the calendar.
 */
function setupLedgerFilters() {
  const rows = ledgerAllRows();
  const labels = ledgerTaskLabels();
  const { first, last } = ledgerDates();

  const select = document.getElementById("ledgerTask");
  if (select) {
    const ids = [...new Set(rows.map((row) => row.task_id).filter(Boolean))].sort();
    select.innerHTML = [
      `<option value="">All tasks</option>`,
      ...ids.map((id) => {
        const label = labels.get(id);
        return `<option value="${esc(id)}">${esc(label ? `${id} · ${label}` : id)}</option>`;
      }),
    ].join("");
    select.value = state.ledgerTask;
  }

  state.ledgerFrom = first;
  state.ledgerTo = last;
  for (const [id, value] of [
    ["ledgerFrom", first],
    ["ledgerTo", last],
  ]) {
    const input = document.getElementById(id);
    if (!input) continue;
    input.min = first;
    input.max = last;
    input.value = value;
  }

  const note = document.getElementById("ledgerRange");
  if (note) {
    note.textContent = first
      ? `Ledger covers ${fmtDate(first)} – ${fmtDate(last)}.`
      : "The ledger has no observations yet.";
  }
}

function renderLedger() {
  const tbody = document.getElementById("ledgerBody");
  if (!tbody) return;

  const labels = ledgerTaskLabels();
  const rows = ledgerAllRows().filter(
    (row) =>
      panelVisible(row.provider_id, row.tier) &&
      (!state.ledgerTask || row.task_id === state.ledgerTask) &&
      (!state.ledgerFrom || (row.date || "") >= state.ledgerFrom) &&
      (!state.ledgerTo || (row.date || "") <= state.ledgerTo),
  );

  if (!rows.length) {
    tbody.innerHTML = emptyRow(
      6,
      "No daily token counts here",
      "No ledger rows match this task, date range, and provider selection.",
    );
    return;
  }

  tbody.innerHTML = rows
    .map((row) => {
      const density = row.density == null ? "—" : Number(row.density).toFixed(1);
      const taskLabel = labels.get(row.task_id);
      const task = taskLabel ? `${row.task_id} · ${taskLabel}` : row.task_id;
      const model = row.model || "—";
      return `<tr class="cofair-table__row">
        <td class="cofair-table__td ledger-col-date">${esc(fmtDate(row.date))}</td>
        <td class="cofair-table__td ledger-col-provider">${providerBadge(row.provider_id)} ${esc(row.tier)}</td>
        <td class="cofair-table__td ledger-col-model" title="${esc(model)}">${esc(model)}</td>
        <td class="cofair-table__td ledger-col-task">${esc(task)}</td>
        <td class="cofair-table__td cofair-table__td--num ledger-col-in">${Number(row.tokens_in || 0).toLocaleString()}</td>
        <td class="cofair-table__td cofair-table__td--num ledger-col-density">${esc(density)}</td>
      </tr>`;
    })
    .join("");
}

function renderTaskTable() {
  const body = document.getElementById("taskBody");
  const rows =
    packTasks()
      .map((task) =>
        taskRowHtml({
          taskId: task.task_id,
          label: task.label,
          probes: task.probes,
          inputChars: task.input_chars,
          inputWords: task.input_words,
        }),
      )
      .join("") + chatTaskRow();

  body.innerHTML =
    rows ||
    `<tr class="cofair-table__row"><td class="cofair-table__td" colspan="3">No tasks in this pack.</td></tr>`;

  body.querySelectorAll(".task-link").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.chat) openChatDrawer(button);
      else openDrawer(button.dataset.task, button);
    });
  });
}

// ---- task drawer -----------------------------------------------------------

function openDrawer(taskId, trigger) {
  const task = (state.eq.tasks || []).find((t) => t.task_id === taskId);
  if (!task) return;
  state.lastFocus = trigger || document.activeElement;

  document.getElementById("drawerEyebrow").textContent = `Task ${task.task_id} · corpus v${task.corpus_version}`;
  document.getElementById("drawerTitle").textContent = task.label;

  // Cross-provider density for this one task, latest day — the comparison the
  // whole index exists to make, scoped to the task being read.
  const runs = (state.eq.token_runs || []).filter((r) => r.task_id === taskId);
  const latestDate = runs.reduce((max, r) => (r.date > max ? r.date : max), "");
  const latest = runs.filter((r) => r.date === latestDate);

  const observed = latest.length
    ? `<table class="cofair-table cofair-table--striped drawer__table">
        <thead class="cofair-table__head">
          <tr class="cofair-table__row">
            <th class="cofair-table__th" scope="col">Provider · tier</th>
            <th class="cofair-table__th" scope="col">Model</th>
            <th class="cofair-table__th cofair-table__th--num" scope="col">In</th>
            <th class="cofair-table__th cofair-table__th--num" scope="col">Out</th>
            <th class="cofair-table__th cofair-table__th--num" scope="col">Tokens / 1K chars</th>
          </tr>
        </thead>
        <tbody class="cofair-table__body">
          ${latest
            .map(
              (r) => `<tr class="cofair-table__row">
              <td class="cofair-table__td">${providerBadge(r.provider_id)} ${esc(r.tier)}</td>
              <td class="cofair-table__td">${esc(r.api_model || r.model_id || "—")}</td>
              <td class="cofair-table__td cofair-table__td--num">${r.tokens_in.toLocaleString()}</td>
              <td class="cofair-table__td cofair-table__td--num">${r.tokens_out.toLocaleString()}</td>
              <td class="cofair-table__td cofair-table__td--num">${r.tokens_in_per_1k_chars.toFixed(1)}</td>
            </tr>`,
            )
            .join("")}
        </tbody>
      </table>
      <p class="cofair-text cofair-text--xs drawer__footnote">
        Observed ${esc(fmtDate(latestDate))}.
      </p>`
    : `<p class="cofair-text cofair-text--small">No completed runs for this task yet.</p>`;

  document.getElementById("drawerBody").innerHTML = `
    <section class="drawer__section">
      <h3 class="cofair-text cofair-text--xs drawer__label">What it probes</h3>
      <p class="cofair-text cofair-text--small">${esc(task.probes)}</p>
    </section>

    <section class="drawer__section">
      <h3 class="cofair-text cofair-text--xs drawer__label">Specification</h3>
      <dl class="drawer__specs">
        <div><dt>Input characters</dt><dd>${task.input_chars.toLocaleString()}</dd></div>
        <div><dt>Input bytes (UTF-8)</dt><dd>${task.input_bytes.toLocaleString()}</dd></div>
        <div><dt>Input words</dt><dd>${task.input_words.toLocaleString()}</dd></div>
        <div><dt>Temperature</dt><dd>0 (where supported)</dd></div>
      </dl>
    </section>

    <section class="drawer__section">
      <h3 class="cofair-text cofair-text--xs drawer__label">Frozen input text</h3>
      <pre class="drawer__prompt"><code>${esc(task.prompt)}</code></pre>
    </section>

    <section class="drawer__section">
      <h3 class="cofair-text cofair-text--xs drawer__label">Latest observed</h3>
      ${observed}
    </section>`;

  document.getElementById("drawerBackdrop").hidden = false;
  const drawer = document.getElementById("taskDrawer");
  drawer.hidden = false;
  requestAnimationFrame(() => drawer.classList.add("drawer--open"));
  document.getElementById("drawerClose").focus();
}

function openChatDrawer(trigger) {
  const transcript = state.eq.chat_transcript || [];
  state.lastFocus = trigger || document.activeElement;
  document.getElementById("drawerEyebrow").textContent =
    `Test 4 · chat corpus v${state.eq.chat_corpus_version || "—"}`;
  document.getElementById("drawerTitle").textContent = "Agent history expansion";

  const latestDate = state.eq.wrapper_runs?.last_date;
  const latest = (state.eq.wrapper_runs?.rows || []).filter(
    (r) => r.run_date === latestDate && r.turn === 10,
  );

  const observed = latest.length
    ? `<table class="cofair-table cofair-table--striped drawer__table">
        <thead class="cofair-table__head">
          <tr class="cofair-table__row">
            <th class="cofair-table__th" scope="col">Provider · tier</th>
            <th class="cofair-table__th cofair-table__th--num" scope="col">Turn-10 prompt tokens</th>
          </tr>
        </thead>
        <tbody class="cofair-table__body">
          ${latest
            .map(
              (r) => `<tr class="cofair-table__row">
              <td class="cofair-table__td">${providerBadge(r.provider_id)} ${esc(r.tier)}</td>
              <td class="cofair-table__td cofair-table__td--num">${Number(r.api_prompt_tokens).toLocaleString()}</td>
            </tr>`,
            )
            .join("")}
        </tbody>
      </table>
      <p class="cofair-text cofair-text--xs drawer__footnote">Observed ${esc(fmtDate(latestDate))}.</p>`
    : `<p class="cofair-text cofair-text--small">No wrapper runs yet.</p>`;

  const body = transcript
    .map(
      (turn, i) =>
        `<div class="drawer__turn"><span class="cofair-text cofair-text--xs">${esc(turn.role)} · ${Math.floor(i / 2) + 1}</span><pre class="drawer__prompt"><code>${esc(turn.text)}</code></pre></div>`,
    )
    .join("");

  document.getElementById("drawerBody").innerHTML = `
    <section class="drawer__section">
      <h3 class="cofair-text cofair-text--xs drawer__label">What it probes</h3>
      <p class="cofair-text cofair-text--small">
        Cumulative prompt-token counts on this frozen transcript expose chat wrappers and
        history packing the provider injects. Assistant text is never regenerated at count time.
      </p>
    </section>
    <section class="drawer__section">
      <h3 class="cofair-text cofair-text--xs drawer__label">Frozen transcript</h3>
      ${body}
    </section>
    <section class="drawer__section">
      <h3 class="cofair-text cofair-text--xs drawer__label">Latest turn-10 counts</h3>
      ${observed}
    </section>`;

  document.getElementById("drawerBackdrop").hidden = false;
  const drawer = document.getElementById("taskDrawer");
  drawer.hidden = false;
  requestAnimationFrame(() => drawer.classList.add("drawer--open"));
  document.getElementById("drawerClose").focus();
}

function closeDrawer() {
  const drawer = document.getElementById("taskDrawer");
  drawer.classList.remove("drawer--open");
  drawer.hidden = true;
  document.getElementById("drawerBackdrop").hidden = true;
  if (state.lastFocus) state.lastFocus.focus();
}

// ---- render ----------------------------------------------------------------

function render() {
  document.getElementById("metricNote").textContent = METRICS[state.metric].note;
  syncProviderChips();
  syncTierChips();
  renderHealth();
  renderLedger();
  renderTaskTable();
  if (trendsPanelVisible()) {
    const { points } = aggregate();
    renderChart(points);
  }
}

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
    if (tab.dataset.tab === "trends") {
      const { points } = aggregate();
      renderChart(points);
    }
  };

  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      e.preventDefault();
      const next =
        e.key === "ArrowRight" ? (i + 1) % tabs.length : (i - 1 + tabs.length) % tabs.length;
      tabs[next].focus();
      activate(tabs[next]);
    });
  });
}

async function main() {
  const res = await fetch("../data/equivalence.json");
  const eq = await res.json();
  state.eq = eq;

  renderProviderChips();
  setupTabs();
  setupLedgerFilters();

  document.getElementById("ledgerTask").addEventListener("change", (e) => {
    state.ledgerTask = e.target.value;
    renderLedger();
  });
  document.getElementById("ledgerFrom").addEventListener("change", (e) => {
    state.ledgerFrom = e.target.value;
    renderLedger();
  });
  document.getElementById("ledgerTo").addEventListener("change", (e) => {
    state.ledgerTo = e.target.value;
    renderLedger();
  });

  document.getElementById("pack").addEventListener("change", (e) => {
    state.pack = e.target.value;
    render();
  });
  document.getElementById("metric").addEventListener("change", (e) => {
    state.metric = e.target.value;
    render();
  });
  document.getElementById("drawerClose").addEventListener("click", closeDrawer);
  document.getElementById("drawerBackdrop").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !document.getElementById("taskDrawer").hidden) closeDrawer();
  });

  render();
}

main();
