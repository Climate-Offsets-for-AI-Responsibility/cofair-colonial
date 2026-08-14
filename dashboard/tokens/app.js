// Token equivalence index — weekly token-consumption drift for a frozen task
// corpus. Reads dashboard/data/equivalence.json (see build_dashboard_data.py).
//
// Provider tinting matches /pricing exactly; every color comes from a
// --cofair-* token, nothing visual is hard-coded here (hub R13).

const Chart = window.Chart;

const state = {
  eq: null,
  selectedProviders: new Set(),
  pack: "suiteLong",
  metric: "tokens_in_per_1k_chars",
  chart: null,
  lastFocus: null,
};

const SERIES_COUNT = 7;

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
    label: "Input tokens per 1,000 characters (meter)",
    axis: "tokens / 1K chars",
    source: "meter",
    note:
      "Tokenizer density from the weekly task meter (median of workhorse replicates). " +
      "A step change on a pinned model is the signature of a silent re-tokenization.",
    decimals: 1,
  },
  ledger_density: {
    label: "Input tokens per 1,000 characters (ledger)",
    axis: "tokens / 1K chars",
    source: "ledger",
    note:
      "Count-only daily ledger on frozen tasks A+B+C (task D weekly). No generation — " +
      "7× the resolution of the meter on the cleanest drift hypothesis.",
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
      "Stochastic and capped. Runs that hit the output cap are censored observations " +
      "(marked below) and understate true verbosity. Workhorse points are medians of N=3.",
    decimals: 0,
  },
  usd: {
    label: "Cost per run",
    axis: "USD",
    source: "meter",
    note:
      "Observed tokens valued at the same-day rate card. Separates price changes from " +
      "consumption changes only when read alongside tokenizer density.",
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

function packTasks() {
  const byId = new Map((state.eq?.tasks || []).map((task) => [task.task_id, task]));
  return packTaskIds().map((id) => byId.get(id)).filter(Boolean);
}

/**
 * Aggregate observed rows into one point per (week|date, provider, tier).
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
  const taskIds = packTaskIds();
  const wanted = new Set(taskIds);
  const groups = new Map();

  for (const row of state.eq?.token_runs || []) {
    if (!wanted.has(row.task_id)) continue;
    if (state.selectedProviders.size && !state.selectedProviders.has(row.provider_id)) continue;

    const key = `${row.week}|${row.provider_id}|${row.tier}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        week: row.week,
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
  complete.sort((a, b) => a.week.localeCompare(b.week));
  return { points: complete, incomplete, xField: "week" };
}

function aggregateLedger() {
  const taskIds = new Set(packTaskIds());
  const groups = new Map();
  for (const row of state.eq?.tokenizer_ledger?.rows || []) {
    if (!taskIds.has(row.task_id)) continue;
    if (state.selectedProviders.size && !state.selectedProviders.has(row.provider_id)) continue;
    if (row.tokens_in_per_1k_chars == null) continue;

    const key = `${row.date}|${row.provider_id}|${row.tier}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        week: row.date,
        provider_id: row.provider_id,
        tier: row.tier,
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

  // For suite packs, require A+B+C present (D is weekly-only on ledger).
  const required = packTaskIds().filter((id) => id !== "D");
  const need = required.length ? required : packTaskIds();

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
  complete.sort((a, b) => a.week.localeCompare(b.week));
  return { points: complete, incomplete, xField: "week" };
}

function aggregateWrapper() {
  const groups = new Map();
  for (const row of state.eq?.wrapper_runs?.rows || []) {
    if (row.turn !== 10) continue;
    if (state.selectedProviders.size && !state.selectedProviders.has(row.provider_id)) continue;
    if (row.api_prompt_tokens == null) continue;
    const key = `${row.run_week}|${row.provider_id}|${row.tier}`;
    groups.set(key, {
      week: row.run_week,
      provider_id: row.provider_id,
      tier: row.tier,
      model_id: row.model_id,
      api_model: row.api_model,
      wrapper_turn10: row.api_prompt_tokens,
    });
  }
  const complete = [...groups.values()].sort((a, b) => a.week.localeCompare(b.week));
  return { points: complete, incomplete: 0, xField: "week" };
}

function renderProviderChips() {
  const el = document.getElementById("providerFilter");
  const providers = Object.keys(state.eq.provider_auth?.requirements || {}).sort();
  el.innerHTML = "";
  providers.forEach((provider) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip cofair-badge";
    chip.dataset.series = String(providerSeries(provider));
    chip.setAttribute("aria-pressed", String(state.selectedProviders.has(provider)));
    chip.innerHTML = `<span class="chip__swatch"></span>${esc(providerLabel(provider))}`;
    chip.addEventListener("click", () => {
      if (state.selectedProviders.has(provider)) state.selectedProviders.delete(provider);
      else state.selectedProviders.add(provider);
      chip.setAttribute("aria-pressed", String(state.selectedProviders.has(provider)));
      render();
    });
    el.appendChild(chip);
  });
}

function renderChart(points) {
  const canvas = document.getElementById("tokenChart");
  const legend = document.getElementById("chartLegend");
  if (!Chart) return;

  const byLine = new Map();
  for (const point of points) {
    const key = `${point.provider_id}|${point.tier}`;
    if (!byLine.has(key)) byLine.set(key, []);
    byLine.get(key).push(point);
  }

  const datasets = [];
  const legendItems = [];
  for (const [key, linePoints] of [...byLine.entries()].sort()) {
    const [providerId, tier] = key.split("|");
    const color = providerColor(providerId);
    const isFlagship = tier === "flagship";
    datasets.push({
      label: `${providerLabel(providerId)} · ${tier}`,
      data: linePoints.map((p) => ({ x: `${p.week}T00:00:00Z`, y: p[state.metric] })),
      borderColor: color,
      backgroundColor: color,
      borderDash: isFlagship ? [] : [5, 4],
      borderWidth: 0.5,
      pointStyle: "circle",
      pointRadius: linePoints.length === 1 ? 3 : 1.5,
      pointHoverRadius: 5,
      pointHitRadius: 10,
      tension: 0,
      spanGaps: true,
    });
    legendItems.push({ providerId, tier, isFlagship });
  }

  legend.innerHTML = legendItems.length
    ? legendItems
        .map(
          (item) => `<li class="chart-legend__item">
            <span class="legend-swatch legend-swatch--${item.isFlagship ? "solid" : "dashed"}"
                  style="--legend-color: var(--cofair-dataviz-${providerSeries(item.providerId)})"></span>
            <span class="cofair-text cofair-text--xs">${esc(providerLabel(item.providerId))} · ${esc(item.tier)}</span>
          </li>`,
        )
        .join("")
    : `<li class="cofair-text cofair-text--xs">No completed runs yet.</li>`;

  const metric = METRICS[state.metric];
  const palette = chartPalette();
  const tickFont = { family: palette.mono, size: palette.sizeXs };
  const labelFont = { family: palette.sans, size: palette.sizeXs };

  // A single observed date collapses the time scale to zero width, which drops
  // every x tick. Pad it to a week so the date the point belongs to stays legible.
  const dates = new Set(points.map((p) => p.week));
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
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: palette.surface,
          borderColor: palette.border,
          borderWidth: 1,
          cornerRadius: palette.radiusSm,
          padding: palette.padding,
          titleColor: palette.text,
          titleFont: labelFont,
          bodyColor: palette.muted,
          bodyFont: tickFont,
          usePointStyle: true,
          boxWidth: 8,
          boxHeight: 8,
          callbacks: {
            title: (items) => fmtDate(String(items[0].raw.x).slice(0, 10)),
            label: (item) => `${item.dataset.label}: ${fmtMetric(item.parsed.y, state.metric)}`,
          },
        },
      },
      scales: {
        x: {
          type: "time",
          time: { unit: "week" },
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
}

/**
 * Test 4 sits alongside the lettered meter tasks as corpus entry E. It is a
 * count-only measurement (no generation), so it has no output cap and is listed
 * regardless of which meter pack is selected.
 */
function chatTaskRow() {
  const transcript = state.eq?.chat_transcript || [];
  if (!transcript.length) return "";
  const text = transcript.map((turn) => turn.text).join("\n");
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  return `<tr class="cofair-table__row">
    <td class="cofair-table__td">
      <button type="button" class="task-link" data-chat="1">E · Test 4 · chat transcript</button>
    </td>
    <td class="cofair-table__td cofair-table__td--num">${text.length.toLocaleString()}</td>
    <td class="cofair-table__td cofair-table__td--num">${words.toLocaleString()}</td>
    <td class="cofair-table__td cofair-table__td--num">count-only</td>
    <td class="cofair-table__td cofair-text cofair-text--small">Chat wrapper and history-packing overhead across 10 frozen turns.</td>
  </tr>`;
}

function renderTaskTable() {
  const body = document.getElementById("taskBody");
  const tasks = packTasks();
  const rows =
    tasks
      .map(
        (task) => `<tr class="cofair-table__row">
      <td class="cofair-table__td">
        <button type="button" class="task-link" data-task="${esc(task.task_id)}">
          ${esc(task.task_id)} · ${esc(task.label)}
        </button>
      </td>
      <td class="cofair-table__td cofair-table__td--num">${task.input_chars.toLocaleString()}</td>
      <td class="cofair-table__td cofair-table__td--num">${task.input_words.toLocaleString()}</td>
      <td class="cofair-table__td cofair-table__td--num">${task.output_cap.toLocaleString()}</td>
      <td class="cofair-table__td cofair-text cofair-text--small">${esc(task.probes)}</td>
    </tr>`,
      )
      .join("") + chatTaskRow();

  body.innerHTML =
    rows ||
    `<tr class="cofair-table__row"><td class="cofair-table__td" colspan="5">No tasks in this pack.</td></tr>`;

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

  // Cross-provider density for this one task, latest week — the comparison the
  // whole index exists to make, scoped to the task being read.
  const runs = (state.eq.token_runs || []).filter((r) => r.task_id === taskId);
  const latestWeek = runs.reduce((max, r) => (r.week > max ? r.week : max), "");
  const latest = runs.filter((r) => r.week === latestWeek);

  const observed = latest.length
    ? `<table class="cofair-table cofair-table--striped drawer__table">
        <thead class="cofair-table__head">
          <tr class="cofair-table__row">
            <th class="cofair-table__th" scope="col">Provider · tier</th>
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
              <td class="cofair-table__td cofair-table__td--num">${r.tokens_in.toLocaleString()}</td>
              <td class="cofair-table__td cofair-table__td--num">${r.tokens_out.toLocaleString()}${
                r.output_censored ? " *" : ""
              }</td>
              <td class="cofair-table__td cofair-table__td--num">${r.tokens_in_per_1k_chars.toFixed(1)}</td>
            </tr>`,
            )
            .join("")}
        </tbody>
      </table>
      <p class="cofair-text cofair-text--xs drawer__footnote">
        Observed ${esc(fmtDate(latestWeek))}. * output hit the cap and is a censored observation.
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
        <div><dt>Output cap</dt><dd>${task.output_cap.toLocaleString()} tokens</dd></div>
        <div><dt>Cadence</dt><dd>${esc(task.cadence)}</dd></div>
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

  const latestWeek = state.eq.wrapper_runs?.last_week;
  const latest = (state.eq.wrapper_runs?.rows || []).filter(
    (r) => r.run_week === latestWeek && r.turn === 10,
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
      <p class="cofair-text cofair-text--xs drawer__footnote">Observed week ${esc(fmtDate(latestWeek))}.</p>`
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
  const { points } = aggregate();

  document.getElementById("metricNote").textContent = METRICS[state.metric].note;
  renderChart(points);
  renderTaskTable();
}

async function main() {
  const res = await fetch("../data/equivalence.json");
  const eq = await res.json();
  state.eq = eq;

  const providers = Object.keys(eq.provider_auth?.requirements || {});
  state.selectedProviders = new Set(providers);

  renderProviderChips();

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
