import { useState, useEffect } from "react";

/* ═══════════════════════════════════════════════════════════════════════════
 *  useBreakpoint
 *  Returns { isMobile, hasHover } — the two environment facts the layout
 *  needs.
 *
 *  isMobile: viewport width below 640px. 640 is the single breakpoint for the
 *  entire app. No tablet tier, no CSS media queries scattered through
 *  component files. Every responsive layout decision reads this one boolean.
 *
 *  hasHover: whether the pointing device can hover at all. This is a separate
 *  question from width and must not be inferred from it — a tablet with a
 *  trackpad hovers, a narrow desktop window hovers, a large touchscreen does
 *  not. Anything revealed on hover has to check this one, or it is simply
 *  unreachable on touch.
 *
 *  Both use matchMedia rather than a resize listener. resize fires on every
 *  pixel of a drag and re-renders each consumer each time, while a media
 *  query listener fires only when the answer actually changes — and hover
 *  capability has no width to listen to in the first place.
 *
 *  Safe to call in multiple components simultaneously — each instance
 *  maintains its own listener but they all resolve to the same value.
 *  There's no SSR concern since this is a pure client-side SPA, but the
 *  guards below keep it renderable under a test renderer with no matchMedia.
 ═══════════════════════════════════════════════════════════════════════════ */
const query = (q) =>
typeof window !== "undefined" && typeof window.matchMedia === "function"
? window.matchMedia(q)
: null;

function useMediaQuery(q, fallback) {
  const [matches, setMatches] = useState(() => query(q)?.matches ?? fallback);

  useEffect(() => {
    const mql = query(q);
    if (!mql) return;
    const handler = (e) => setMatches(e.matches);
    // addListener is the pre-2019 spelling; Safari needed it until 14.
    if (mql.addEventListener) mql.addEventListener("change", handler);
    else mql.addListener(handler);
    setMatches(mql.matches);
    return () => {
      if (mql.removeEventListener) mql.removeEventListener("change", handler);
      else mql.removeListener(handler);
    };
  }, [q]);

  return matches;
}

export function useBreakpoint() {
  const isMobile = useMediaQuery("(max-width: 639px)", false);
  // Defaults to true when unknown: showing a control that could have been
  // hidden is a cosmetic flaw, hiding one that can never be revealed is a
  // feature the user cannot reach.
  const hasHover = useMediaQuery("(hover: hover)", true);
  return { isMobile, hasHover };
}
