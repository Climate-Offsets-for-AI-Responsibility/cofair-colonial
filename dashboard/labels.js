/** Pure label / hover helpers for the pricing trend chart. */

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

function pointDate(point) {
  return typeof point.x === "string" ? point.x : "";
}

export function priceAtOrBefore(points, dateStr) {
  let price = null;
  for (const p of points) {
    const d = pointDate(p);
    if (!d || d > dateStr) continue;
    price = p.y;
  }
  return price;
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
