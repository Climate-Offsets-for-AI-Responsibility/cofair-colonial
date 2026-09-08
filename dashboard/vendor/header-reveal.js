/**
 * Scroll-reveal for the sticky header's Sign up control and lockup tagline.
 * Zero-build surfaces (colonial /pricing and /tokens) load this module;
 * React SiteHeader calls the same mount after paint.
 */
export function headerSignupShouldReveal(sentinelBottomInViewport) {
  return sentinelBottomInViewport <= 0;
}

export function applyHeaderSignupReveal(el, shouldReveal) {
  el.classList.toggle("cofair-site-header__actions--visible", shouldReveal);
  el.classList.toggle("cofair-site-header__reveal--visible", shouldReveal);
}

export function mountHeaderSignupReveal(header, options = {}) {
  const revealEls = [
    ...header.querySelectorAll(".cofair-site-header__actions"),
    ...header.querySelectorAll(".cofair-site-header__tagline"),
  ];
  if (revealEls.length === 0) return () => {};

  for (const el of revealEls) {
    if (el.classList.contains("cofair-site-header__actions")) {
      el.classList.add("cofair-site-header__actions--reveal");
    }
    el.classList.add("cofair-site-header__reveal");
  }

  const sentinelSelector = options.sentinelSelector ?? ".hero";
  const fallbackVh = options.fallbackVh ?? 0.5;

  function measure() {
    const sentinel = document.querySelector(sentinelSelector);
    const shouldReveal = sentinel
      ? headerSignupShouldReveal(sentinel.getBoundingClientRect().bottom)
      : window.scrollY >= window.innerHeight * fallbackVh;
    for (const el of revealEls) applyHeaderSignupReveal(el, shouldReveal);
  }

  measure();

  const sentinel = document.querySelector(sentinelSelector);
  const io =
    sentinel && typeof IntersectionObserver === "function"
      ? new IntersectionObserver(measure)
      : null;
  if (io && sentinel) io.observe(sentinel);

  window.addEventListener("scroll", measure, { passive: true });
  window.addEventListener("resize", measure);

  return () => {
    io?.disconnect();
    window.removeEventListener("scroll", measure);
    window.removeEventListener("resize", measure);
  };
}
