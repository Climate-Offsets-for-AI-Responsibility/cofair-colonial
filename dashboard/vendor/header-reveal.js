/**
 * Scroll-reveal for the sticky header's Sign up control.
 * Zero-build surfaces (colonial /pricing and /tokens) load this module;
 * React SiteHeader calls the same mount after paint.
 */
export function headerSignupShouldReveal(sentinelBottomInViewport) {
  return sentinelBottomInViewport <= 0;
}

export function applyHeaderSignupReveal(actionsEl, shouldReveal) {
  actionsEl.classList.toggle("cofair-site-header__actions--visible", shouldReveal);
}

export function mountHeaderSignupReveal(header, options = {}) {
  const actions = header.querySelector(".cofair-site-header__actions");
  if (!actions) return () => {};

  actions.classList.add("cofair-site-header__actions--reveal");

  const sentinelSelector = options.sentinelSelector ?? ".hero";
  const fallbackVh = options.fallbackVh ?? 0.5;

  function measure() {
    const sentinel = document.querySelector(sentinelSelector);
    if (sentinel) {
      applyHeaderSignupReveal(
        actions,
        headerSignupShouldReveal(sentinel.getBoundingClientRect().bottom),
      );
      return;
    }
    applyHeaderSignupReveal(actions, window.scrollY >= window.innerHeight * fallbackVh);
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
