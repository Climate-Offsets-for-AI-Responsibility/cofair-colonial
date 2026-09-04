/** Pure label / hover / color helpers shared by the trend charts. */

export function hexToHsl(hex) {
  const raw = String(hex ?? "").replace("#", "").trim();
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

const STARTING_BYLINE = /\s+starting\s+[A-Za-z]+ \d{1,2}, \d{4}/g;
const THROUGH_BYLINE = /\s+through\s+[A-Za-z]+ \d{1,2}, \d{4}/g;

export function scrubDisplayName(name) {
  return String(name ?? "")
    .replace(STARTING_BYLINE, "")
    .replace(THROUGH_BYLINE, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function isFutureSchedulePlaceholder(row) {
  const id = String(row?.model_id ?? "").toLowerCase();
  const name = String(row?.display_name ?? "").toLowerCase();
  return id.includes("starting-") || /\bstarting\s+[a-z]+ \d{1,2}, \d{4}/.test(name);
}

export function effectiveModality(modality) {
  return modality || "text";
}

export function prettyModality(modality) {
  return String(modality ?? "");
}

export function prettyContext(contextWindow) {
  const raw = String(contextWindow ?? "");
  if (raw === "short_context") return "short context";
  if (raw === "long_context") return "long context";
  return raw;
}

export function formatLegendLabel(row, { showContext = false } = {}) {
  const name = scrubDisplayName(row.display_name);
  const extras = [];
  if (row.modality) extras.push(prettyModality(row.modality));
  if (showContext && row.context_window) extras.push(prettyContext(row.context_window));
  return extras.length ? `${name} (${extras.join(", ")})` : name;
}

export function contextsNeedDisambiguation(rows) {
  const seen = new Map();
  for (const row of rows) {
    const key = `${row.provider_id}|${row.model_id}`;
    if (!seen.has(key)) seen.set(key, new Set());
    if (row.context_window) seen.get(key).add(row.context_window);
  }
  const need = new Set();
  for (const [key, windows] of seen) {
    if (windows.size > 1) need.add(key);
  }
  return need;
}

/**
 * The ISO day a point sits on.
 *
 * Sliced to 10 characters because the two charts carry `x` differently —
 * /pricing plots plain `YYYY-MM-DD`, /tokens plots a full `…T00:00:00Z` so its
 * time scale reads UTC. Compared whole, a timestamp always sorts after the bare
 * date it belongs to, which silently dropped every value on the hovered day.
 */
function pointDate(point) {
  return typeof point.x === "string" ? point.x.slice(0, 10) : "";
}

export function pointAtOrBefore(points, dateStr) {
  let found = null;
  for (const p of points) {
    const d = pointDate(p);
    if (!d || d > dateStr) continue;
    found = p;
  }
  return found;
}

export function priceAtOrBefore(points, dateStr) {
  return pointAtOrBefore(points, dateStr)?.y ?? null;
}

export function sortDatasetsForDate(datasets, dateStr) {
  return [...datasets].sort((a, b) => {
    const pa = priceAtOrBefore(a.data, dateStr);
    const pb = priceAtOrBefore(b.data, dateStr);
    if (pa == null && pb == null) return 0;
    if (pa == null) return 1;
    if (pb == null) return -1;
    return pb - pa;
  });
}

/**
 * Where a series identity changes from one observation to the next.
 *
 * `true` at index `i` means "the thing measured at `i` is not the thing measured
 * at `i - 1`", which is what the trend chart breaks its line on.
 *
 * `requireCurrent` guards the derived bases (fit basis, corpus): a point whose
 * own basis is unrecorded cannot assert a change, so a value going *missing*
 * leaves the line intact. A basis appearing where there was none still breaks —
 * that is the case where a day's prompts or fit inputs are newly pinned and the
 * two halves genuinely are not comparable. Model identity carries no guard: it
 * has been recorded since the first run, so either direction is a real break.
 */
export function basisChangeFlags(values, { requireCurrent = false } = {}) {
  const list = values || [];
  return list.map(
    (value, i) =>
      i > 0 && value !== list[i - 1] && (!requireCurrent || Boolean(value)),
  );
}

/**
 * The segment boundaries for one provider-tier line, in observation order.
 *
 * Three independent things can make two adjacent points incomparable, and all
 * three have to break the line rather than be drawn as drift:
 *
 * - `model` — the provider re-pinned the panel's flagship or workhorse, so the
 *   series continues under a different model. This is the one the reader sees
 *   most often, because flagship and workhorse both version over time.
 * - `fitBasis` — the fitted overhead/content split was estimated from a
 *   different task set, so the parameter is not quite the same quantity.
 * - `corpus` — the prompts themselves were replaced, so a pack total is a sum
 *   over different text. The strongest break: the other two mean the
 *   measurement moved, this one means the thing measured did.
 *
 * `model` is compared as a *basis* — the set of models the point aggregates, not
 * whichever row happened to be summed first. A day that served two model
 * versions across one pack is genuinely a different identity from a day that
 * served one, and reading the first row alone hid exactly that.
 */
export function computeSeriesBreaks(points) {
  const list = points || [];
  const modelAt = list.map((p) => p?.model_basis || p?.api_model || p?.model_id || "");
  const newModelAt = basisChangeFlags(modelAt);
  const newBasisAt = basisChangeFlags(
    list.map((p) => p?.fit_basis || ""),
    { requireCurrent: true },
  );
  const newCorpusAt = basisChangeFlags(
    list.map((p) => p?.corpus_basis || ""),
    { requireCurrent: true },
  );
  const breakAt = newModelAt.map((changed, i) => changed || newBasisAt[i] || newCorpusAt[i]);
  return { modelAt, newModelAt, newBasisAt, newCorpusAt, breakAt };
}

export function parseOptionalNumber(value) {
  if (value == null) return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatEstimatedSpend(usd) {
  const value = parseOptionalNumber(usd);
  if (value == null) return "—";
  const abs = Math.abs(value);
  const decimals = value === 0 ? 2 : abs >= 1 ? 2 : abs >= 0.01 ? 4 : 6;
  const formatter = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return formatter.format(value);
}

export function formatCostDelta(comparison) {
  if (!comparison) return "Comparison unavailable";
  if (comparison.status === "new_baseline") return "New baseline";
  if (comparison.status !== "ok") return "Comparison unavailable";

  const deltaPct = parseOptionalNumber(comparison.delta_pct);
  const deltaUsd = parseOptionalNumber(comparison.delta_usd);
  if (deltaUsd == null) return "Comparison unavailable";
  if (deltaUsd === 0) return `No change · ${formatEstimatedSpend(0)}`;
  if (deltaPct == null) {
    return "Comparison unavailable";
  }
  const sign = deltaUsd > 0 ? "+" : "-";
  const pct = `${sign}${Math.abs(deltaPct).toFixed(1)}%`;
  const usd = `${sign}${formatEstimatedSpend(Math.abs(deltaUsd))}`;
  const direction = deltaUsd > 0 ? "Increase" : "Decrease";
  return `${direction} · ${pct} · ${usd}`;
}

// `splitLeafCostColumns` removed 2026-09-04 with the request-level rows it fed.
// Costs is a flat date/provider/model/task table now, and the builder already
// publishes those four cost columns on the task node, so mapping a single
// request's `estimated_cost_usd` onto a column by its source had no caller left.

export function resolveCostDetailRequestState({ hasCachedDetail, hasPath }) {
  if (hasCachedDetail) {
    return {
      shouldFetch: false,
      loading: false,
      error: null,
    };
  }
  if (!hasPath) {
    return {
      shouldFetch: false,
      loading: false,
      error: "No detail path published for this run date.",
    };
  }
  return {
    shouldFetch: true,
    loading: true,
    error: null,
  };
}

// `formatCostDetailWithheldStatus` removed 2026-09-04 along with the day-level
// status sentence it wrote. Completeness is still enforced and still stated: the
// cards read from `costs.comparisons`, which withholds a period total until
// every scheduled request in it is priced exactly once (D81), and each table row
// carries its own "Incomplete" note plus the counts behind it. What is gone is
// the second, whole-day restatement of that above the table.

function panelKey(row) {
  return `${row?.provider_id || ""}|${row?.tier || ""}`;
}

export function formatSuiteBaselineAwaitingStatus({
  pack,
  metricSource,
  dashboardStartDate,
  today,
  latestCompleteDate,
  latestRunDate,
  chatCorpusVersion,
  panelRows,
  latestRunRows,
}) {
  if (pack !== "suiteLong" || metricSource !== "meter") return "";
  if (!dashboardStartDate || !today || !chatCorpusVersion) return "";
  if (dashboardStartDate >= today) return "";
  if (latestCompleteDate) return "";
  if (!latestRunDate || latestRunDate < dashboardStartDate) return "";

  const panel = new Set((panelRows || []).map(panelKey).filter(Boolean));
  if (!panel.size) return "";

  const rows = latestRunRows || [];
  const hasAnyPostEpochSuiteRows = rows.some(
    (row) =>
      row?.run_status === "ok" &&
      row?.task_id &&
      row.task_id !== "E" &&
      panel.has(panelKey(row)),
  );
  if (!hasAnyPostEpochSuiteRows) return "";

  const eReady = new Set(
    rows
      .filter(
        (row) =>
          row?.run_status === "ok" &&
          row?.task_id === "E" &&
          panel.has(panelKey(row)) &&
          row?.canonical !== false &&
          (row?.chat_corpus_version || chatCorpusVersion) === chatCorpusVersion,
      )
      .map(panelKey),
  );
  const missingCount = [...panel].filter((key) => !eReady.has(key)).length;
  if (!missingCount) return "";

  const rowsText = missingCount === 1 ? "row is" : "rows are";
  return (
    "Complete A-F baseline is still awaiting collection: " +
    `${missingCount} panel ${rowsText} missing canonical task E rows ` +
    `for chat corpus v${chatCorpusVersion} on the latest run date.`
  );
}

export function drawerObservedColumns(taskId) {
  if (taskId === "E") {
    return [
      { key: "tokens_in", label: "In" },
      { key: "tokens_out", label: "Out" },
      { key: "tokens_total", label: "Total" },
    ];
  }
  return [
    { key: "tokens_in", label: "In" },
    { key: "tokens_out", label: "Out" },
    { key: "tokens_in_per_1k_chars", label: "Tokens / 1K chars" },
  ];
}
