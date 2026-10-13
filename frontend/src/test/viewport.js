/* ═══════════════════════════════════════════════════════════════════════════
 * VIEWPORT
 * stubViewport(width) makes window.matchMedia answer as a window `width`
 * pixels wide, with a pointer that can hover: each `(max-width: Npx)` query
 * matches when width <= N, `(hover: hover)` matches, and anything else does
 * not. Those two kinds are what useBreakpoint asks.
 *
 * Why one helper: the tests that needed a viewport each wrote their own
 * stand-in. Three were the same comparison written out separately, one of
 * them with its width fixed inline, and one answered every max-width query
 * as matching whatever its number, and hover as not matching. A new kind of
 * query, or a fix to how one is answered, now has one place to go.
 *
 * The listeners never fire, so stubbing again after a render does not reach
 * a component already mounted: stub before rendering. vi.unstubAllGlobals()
 * undoes it.
 *
 * Verified by mutation: the comparison off by one pixel, and the width
 * ignored so that every max-width query matches, are each killed by the
 * boundary tests in useBreakpoint.test.js and the header tests in
 * App.test.jsx.
 ═══════════════════════════════════════════════════════════════════════════ */
import { vi } from "vitest";

/** A viewport `width` pixels wide, with a pointer that can hover. */
export function stubViewport(width) {
  vi.stubGlobal("matchMedia", (q) => {
    const max = /\(max-width:\s*(\d+)px\)/.exec(q);
    return {
      matches: max ? width <= Number(max[1]) : q === "(hover: hover)",
      media: q,
      addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {},
    };
  });
}
