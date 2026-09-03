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

export function formatEstimatedSpend(usd) {
  const value = Number(usd);
  if (!Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  const decimals = abs >= 1 ? 2 : abs >= 0.01 ? 4 : 6;
  const formatted = abs.toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return `${value < 0 ? "-" : ""}$${formatted}`;
}

export function formatCostDelta(comparison) {
  if (!comparison || comparison.status !== "ok") return "Comparison unavailable";
  const deltaPct = Number(comparison.delta_pct);
  const deltaUsd = Number(comparison.delta_usd);
  if (!Number.isFinite(deltaPct) || !Number.isFinite(deltaUsd)) {
    return "Comparison unavailable";
  }
  const pct = `${deltaPct >= 0 ? "+" : ""}${deltaPct.toFixed(1)}%`;
  const usd = formatEstimatedSpend(deltaUsd);
  return `${pct} · ${deltaUsd >= 0 ? "+" : ""}${usd}`;
}
