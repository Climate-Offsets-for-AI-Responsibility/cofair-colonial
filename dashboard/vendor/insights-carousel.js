/**
 * Insights carousel — shared by the React marketing surface and zero-build
 * colonial dashboards (same vanilla-mount pattern as header-reveal.js).
 */

const DATE = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

const DEFAULT_FEED = "https://cofair.org/insights.json";
/** Ghost-backed feed. Falls back to the committed JSON when this is not deployed yet. */
const LIVE_FEED = "https://cofair.org/api/insights-feed";
const DEFAULT_INDEX = "https://cofair.org/insights/";
const LOCAL_DEV_FEED = "http://localhost:5174/insights.json";

export const CAROUSEL_LIMIT = 5;
export const HIDDEN_SLUGS = new Set(["testing-cofair-integration"]);

/** When colonial is served locally, production `insights.json` is not deployed yet. */
export function localInsightsFeedFallback(hostname, feedUrl) {
  if (hostname !== "localhost" && hostname !== "127.0.0.1") return null;
  if (!feedUrl || feedUrl === DEFAULT_FEED || feedUrl.startsWith("https://cofair.org/")) {
    return LOCAL_DEV_FEED;
  }
  return null;
}

export function formatCarouselDate(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.valueOf())) return "";
  return DATE.format(date);
}

export function carouselStatus(index, total) {
  if (!total) return "";
  return `${index + 1} of ${total}`;
}

export function stepCarouselIndex(index, delta, total) {
  if (total <= 0) return 0;
  const next = index + delta;
  if (next < 0) return 0;
  if (next > total - 1) return total - 1;
  return next;
}

/** Scroll the track inside its overflow viewport — never the page. */
export function scrollCarouselViewportToIndex(root, index, { behavior = "auto" } = {}) {
  const viewport = root.querySelector(".cofair-insights-carousel__viewport");
  const items = root.querySelectorAll(".cofair-insights-carousel__item");
  const target = items[index];
  if (!viewport || !target || typeof viewport.scrollTo !== "function") return;
  viewport.scrollTo({ left: target.offsetLeft, behavior });
}

export function escapeCarouselText(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Drop the weekly-brief series prefix so tiles and article heads share one title. */
export function displayCarouselTitle(title) {
  return String(title ?? "")
    .replace(/^(?:Municipal(?: Data Center)?|Community) Briefings?:\s*/i, "")
    .trim();
}

function slugFromHref(href) {
  try {
    const path = new URL(href, "https://cofair.org").pathname;
    return path.replace(/\/+$/, "").split("/").pop() ?? "";
  } catch {
    return "";
  }
}

function tagsMarkup(tags) {
  if (!tags?.length) return "";
  const items = tags
    .map(
      (tag) =>
        `<li><span class="cofair-badge">${escapeCarouselText(tag)}</span></li>`,
    )
    .join("");
  return `<ul class="cofair-insights-carousel__tags" aria-label="Topics">${items}</ul>`;
}

/** Stable stand-in until live comment counts are wired. */
export function placeholderCommentCount(href) {
  const value = String(href ?? "");
  let n = 0;
  for (let i = 0; i < value.length; i += 1) n = (n + value.charCodeAt(i) * (i + 1)) % 97;
  return 4 + (n % 42);
}

function commentsMarkup(href) {
  const count = placeholderCommentCount(href);
  return `<p class="cofair-insights-carousel__comments" aria-label="${count} comments"><svg class="cofair-insights-carousel__comment-icon" viewBox="0 0 24 24" aria-hidden="true" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg><span>${count}</span></p>`;
}

function imageMarkup(item) {
  if (!item.imageSrc) return "";
  return `<span class="tile-media"><img class="cofair-insights-carousel__image" src="${escapeCarouselText(item.imageSrc)}" alt="${escapeCarouselText(item.imageAlt ?? "")}" /></span>`;
}

export function renderCarouselCard(item) {
  const date = formatCarouselDate(item.publishedAt);
  const title = escapeCarouselText(item.title);
  const excerpt = escapeCarouselText(item.excerpt);
  const href = escapeCarouselText(item.href);
  return `<article class="cofair-insights-carousel__card">
  <a class="cofair-insights-carousel__link" href="${href}">
    ${imageMarkup(item)}
    <div class="cofair-insights-carousel__body">
      <h3 class="cofair-insights-carousel__title">${title}</h3>
      <p class="cofair-insights-carousel__excerpt">${excerpt}</p>
      ${tagsMarkup(item.tags)}
      <div class="cofair-insights-carousel__meta">
        ${date ? `<time class="cofair-insights-carousel__date" datetime="${escapeCarouselText(item.publishedAt)}">${date}</time>` : ""}
        ${commentsMarkup(item.href)}
      </div>
    </div>
  </a>
</article>`;
}

export function renderCarouselTrack(items) {
  return items
    .map((item) => `<li class="cofair-insights-carousel__item">${renderCarouselCard(item)}</li>`)
    .join("");
}

function feedNavigationHref(href, baseUrl) {
  if (baseUrl === undefined) return href;
  const base = baseUrl.replace(/\/+$/, "");
  if (href.startsWith("/") && !href.startsWith("//")) return `${base}${href}`;
  try {
    const url = new URL(href);
    if (["cofair.org", "www.cofair.org"].includes(url.hostname) && ["http:", "https:"].includes(url.protocol)) {
      return `${base}${url.pathname}${url.search}${url.hash}`;
    }
  } catch {
    // Leave anchors and offsite resources unchanged.
  }
  return href;
}

export function parseInsightsFeed(payload, baseUrl) {
  const posts = Array.isArray(payload?.posts) ? payload.posts : [];
  return posts
    .map((post) => ({
      href: feedNavigationHref(post.href ?? post.canonical ?? "", baseUrl),
      title: displayCarouselTitle(post.title ?? ""),
      excerpt: post.excerpt ?? "",
      tags: Array.isArray(post.tags) ? post.tags : [],
      publishedAt: post.publishedAt ?? "",
      imageSrc: post.featureImage?.src ?? post.imageSrc ?? "",
      imageAlt: post.featureImage?.alt ?? post.imageAlt ?? "",
    }))
    .filter((item) => item.href && item.title)
    .filter((item) => !HIDDEN_SLUGS.has(slugFromHref(item.href)))
    .filter((item) => Boolean(item.imageSrc))
    .slice(0, CAROUSEL_LIMIT);
}

function applyIndex(root, index, total, { instant = false } = {}) {
  const status = root.querySelector(".cofair-insights-carousel__status");
  if (status) status.textContent = carouselStatus(index, total);
  const prev = root.querySelector(".cofair-insights-carousel__prev");
  const next = root.querySelector(".cofair-insights-carousel__next");
  if (prev) prev.disabled = index <= 0;
  if (next) next.disabled = index >= total - 1;
  scrollCarouselViewportToIndex(root, index, {
    behavior: instant ? "auto" : "smooth",
  });
}

function fillTrack(root, items) {
  const track = root.querySelector(".cofair-insights-carousel__track");
  if (!track) return;
  track.innerHTML = renderCarouselTrack(items);
}

export function mountInsightsCarousel(root, options = {}) {
  if (!root) return () => {};

  let index = 0;
  let items = options.items ?? null;

  function wire(total) {
    const onPrev = () => {
      index = stepCarouselIndex(index, -1, total);
      applyIndex(root, index, total);
    };
    const onNext = () => {
      index = stepCarouselIndex(index, 1, total);
      applyIndex(root, index, total);
    };
    const prev = root.querySelector(".cofair-insights-carousel__prev");
    const next = root.querySelector(".cofair-insights-carousel__next");
    prev?.addEventListener("click", onPrev);
    next?.addEventListener("click", onNext);
    applyIndex(root, index, total, { instant: true });
    return () => {
      prev?.removeEventListener("click", onPrev);
      next?.removeEventListener("click", onNext);
    };
  }

  const alreadyRendered = root.querySelector(".cofair-insights-carousel__item");
  const feedUrl = options.feedUrl ?? root.dataset.feed ?? DEFAULT_FEED;
  let cancelled = false;
  let unwire = () => {};

  function show(nextItems) {
    unwire();
    items = nextItems;
    index = 0;
    if (!items.length) {
      root.hidden = true;
      return;
    }
    root.hidden = false;
    fillTrack(root, items);
    unwire = wire(items.length);
  }

  if (items?.length) {
    if (!alreadyRendered) fillTrack(root, items);
    unwire = wire(items.length);
  } else if (alreadyRendered) {
    unwire = wire(root.querySelectorAll(".cofair-insights-carousel__item").length);
  }

  function loadFeed(url) {
    return fetch(url).then((response) => {
      if (!response.ok) throw new Error(`insights feed HTTP ${response.status}`);
      return response.json();
    });
  }

  const fallbackUrl = localInsightsFeedFallback(
    globalThis.location?.hostname ?? "",
    feedUrl,
  );
  const urls = [];
  if (feedUrl !== LIVE_FEED) urls.push(LIVE_FEED);
  urls.push(feedUrl);
  if (fallbackUrl && !urls.includes(fallbackUrl)) urls.push(fallbackUrl);

  const firstLoad = urls.reduce(
    (attempt, url) => attempt.catch(() => loadFeed(url)),
    Promise.reject(new Error("insights feed not loaded")),
  );

  firstLoad
    .then((payload) => {
      if (cancelled) return;
      const next = parseInsightsFeed(payload, options.baseUrl);
      if (!next.length) {
        if (!items?.length) root.hidden = true;
        return;
      }
      show(next);
    })
    .catch(() => {
      if (!cancelled && !items?.length && !alreadyRendered) root.hidden = true;
    });

  return () => {
    cancelled = true;
    unwire();
  };
}

export { DEFAULT_FEED, DEFAULT_INDEX, LIVE_FEED, LOCAL_DEV_FEED };
