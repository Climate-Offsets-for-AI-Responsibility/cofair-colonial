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
    .replace(/^Municipal(?: Data Center)? Briefing:\s*/i, "")
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

function imageMarkup(item) {
  if (!item.imageSrc) return "";
  return `<img class="cofair-insights-carousel__image" src="${escapeCarouselText(item.imageSrc)}" alt="${escapeCarouselText(item.imageAlt ?? "")}" />`;
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
      ${tagsMarkup(item.tags)}
      <h3 class="cofair-insights-carousel__title">${title}</h3>
      <p class="cofair-insights-carousel__excerpt">${excerpt}</p>
      ${date ? `<time class="cofair-insights-carousel__date" datetime="${escapeCarouselText(item.publishedAt)}">${date}</time>` : ""}
    </div>
  </a>
</article>`;
}

export function renderCarouselTrack(items) {
  return items
    .map((item) => `<li class="cofair-insights-carousel__item">${renderCarouselCard(item)}</li>`)
    .join("");
}

export function parseInsightsFeed(payload) {
  const posts = Array.isArray(payload?.posts) ? payload.posts : [];
  return posts
    .map((post) => ({
      href: post.href ?? post.canonical ?? "",
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
  if (items?.length) {
    if (!alreadyRendered) fillTrack(root, items);
    return wire(items.length);
  }
  if (alreadyRendered) {
    return wire(root.querySelectorAll(".cofair-insights-carousel__item").length);
  }

  const feedUrl = options.feedUrl ?? root.dataset.feed ?? DEFAULT_FEED;
  let cancelled = false;
  let unwire = () => {};

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
  const firstLoad = loadFeed(feedUrl).catch((error) => {
    if (fallbackUrl && fallbackUrl !== feedUrl) return loadFeed(fallbackUrl);
    throw error;
  });

  firstLoad
    .then((payload) => {
      if (cancelled) return;
      items = parseInsightsFeed(payload);
      if (items.length === 0) {
        root.hidden = true;
        return;
      }
      fillTrack(root, items);
      unwire = wire(items.length);
    })
    .catch(() => {
      if (!cancelled) root.hidden = true;
    });

  return () => {
    cancelled = true;
    unwire();
  };
}

export { DEFAULT_FEED, DEFAULT_INDEX, LOCAL_DEV_FEED };
