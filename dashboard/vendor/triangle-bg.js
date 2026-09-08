/**
 * Vanilla mount of the React TriangleBackground painter.
 * Zero-build surfaces (colonial /pricing and /tokens) cannot import the
 * React component; they load this module and stamp `data-cofair-triangles`
 * on the same `.cofair-triangle-bg` shell.
 */
const DEFAULTS = {
  columns: 12,
  maxOpacity: 0.28,
  animatedRatio: 0.14,
  minPulseDuration: 10,
  maxPulseDuration: 18,
  palette: [
    "var(--cofair-color-primary-hover)",
    "var(--cofair-color-primary-active)",
    "var(--cofair-color-primary)",
    "var(--cofair-color-primary-border)",
    "var(--cofair-color-success)",
  ],
};

function buildCellTriangles(size, palette) {
  const backslash = Math.random() < 0.5;
  const tl = "0,0";
  const tr = `${size},0`;
  const br = `${size},${size}`;
  const bl = `0,${size}`;
  const pairs = backslash
    ? [
        [tl, tr, br],
        [tl, br, bl],
      ]
    : [
        [tl, tr, bl],
        [tr, br, bl],
      ];
  return pairs.map((pts) => ({
    points: pts.join(" "),
    color: palette[Math.floor(Math.random() * palette.length)],
  }));
}

function randomDuration(settings) {
  const { minPulseDuration: min, maxPulseDuration: max } = settings;
  return min + Math.random() * Math.max(0, max - min);
}

function pickIndex(total, exclude) {
  if (total === 0) return 0;
  let idx = Math.floor(Math.random() * total);
  let guard = 0;
  while (exclude.has(idx) && guard < 50) {
    idx = Math.floor(Math.random() * total);
    guard += 1;
  }
  return idx;
}

function layoutTriangles(width, height, settings) {
  const cellSize = width / settings.columns;
  if (cellSize <= 0) return [];
  const rows = Math.ceil(height / cellSize) + 1;
  const offsetY = (height - rows * cellSize) / 2;
  const result = [];
  for (let r = 0; r < rows; r += 1) {
    for (let c = 0; c < settings.columns; c += 1) {
      for (const t of buildCellTriangles(cellSize, settings.palette)) {
        result.push({
          ...t,
          x: c * cellSize,
          y: r * cellSize + offsetY,
        });
      }
    }
  }
  return result;
}

export function mountTriangleBackground(el, config = {}) {
  const settings = { ...DEFAULTS, ...config };
  el.style.backgroundColor = settings.baseColor ?? "var(--cofair-color-primary)";
  el.style.setProperty("--cofair-triangle-idle-opacity", String(settings.maxOpacity));

  let svg = el.querySelector(":scope > .cofair-triangle-bg__svg");
  if (!svg) {
    svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "cofair-triangle-bg__svg");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    el.prepend(svg);
  }

  const recent = [];
  let polygons = [];
  const active = new Map();

  function seedActive() {
    active.clear();
    recent.length = 0;
    if (polygons.length === 0) return;
    const count = Math.max(1, Math.floor(polygons.length * settings.animatedRatio));
    const taken = new Set();
    while (active.size < count) {
      const idx = pickIndex(polygons.length, taken);
      taken.add(idx);
      active.set(idx, randomDuration(settings));
    }
  }

  function paint() {
    const width = el.clientWidth;
    const height = el.clientHeight;
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    const triangles = layoutTriangles(width, height, settings);
    svg.replaceChildren();
    polygons = triangles.map((tri) => {
      const polygon = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      polygon.setAttribute("points", tri.points);
      polygon.setAttribute("fill", tri.color);
      polygon.setAttribute("transform", `translate(${tri.x} ${tri.y})`);
      polygon.style.opacity = String(settings.maxOpacity);
      svg.appendChild(polygon);
      return polygon;
    });
    seedActive();
    applyActive();
  }

  function applyActive() {
    polygons.forEach((polygon, index) => {
      const duration = active.get(index);
      if (duration === undefined) {
        polygon.removeAttribute("class");
        polygon.style.opacity = String(settings.maxOpacity);
        polygon.onanimationend = null;
        return;
      }
      polygon.setAttribute("class", "cofair-triangle-bg__cell--pulse");
      polygon.style.animationDuration = `${duration}s`;
      polygon.onanimationend = () => {
        active.delete(index);
        const maxRecent = Math.max(1, Math.floor(polygons.length * 0.03));
        recent.push(index);
        if (recent.length > maxRecent) recent.splice(0, recent.length - maxRecent);
        const exclude = new Set([...active.keys(), ...recent, index]);
        const replacement =
          exclude.size >= polygons.length
            ? pickIndex(polygons.length, new Set(active.keys()))
            : pickIndex(polygons.length, exclude);
        active.set(replacement, randomDuration(settings));
        applyActive();
      };
    });
  }

  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  if (reduced) {
    settings.animatedRatio = 0;
  }

  const observer = new ResizeObserver(() => paint());
  observer.observe(el);
  paint();
  return () => observer.disconnect();
}
