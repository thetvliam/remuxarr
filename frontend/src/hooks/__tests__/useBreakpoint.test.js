/**
 * useBreakpoint — where the layout and the header change.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The breakpoint was 640px, where the desktop header did not fit. Measured
 * in Chrome at its natural width it needs 841px to 985px at rest, depending
 * on the theme, so from 640 up its buttons were squeezed until their labels
 * wrapped, and below 765px its right-hand controls ran off the screen. The
 * page layout now changes at 860px, and the header has a compact form below
 * 1140px. Both numbers come from measurement (see the hook's own comment),
 * and nothing tested either: the old number survived every test.
 *
 * matchMedia here answers each max-width query against a chosen viewport
 * width (stubViewport, src/test/viewport.js), so these pin the widths at
 * which the answers change rather than the spelling of the queries. The
 * pairs either side of each boundary are what make an off-by-one show.
 *
 * Verified by mutation: isMobile back at 640, and isCompact at 1000 (the
 * figure first agreed, before the header was measured properly), both
 * survived the suite without this file and are killed by it.
 */
import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { stubViewport } from "../../test/viewport";
import { useBreakpoint } from "../useBreakpoint";

const at = (width) => {
  stubViewport(width);
  return renderHook(() => useBreakpoint()).result.current;
};

afterEach(() => { vi.unstubAllGlobals(); });

describe("useBreakpoint — the page layout", () => {
  it("is the phone layout up to 859px", () => {
    expect(at(390).isMobile).toBe(true);
    expect(at(859).isMobile).toBe(true);
  });

  it("is the desktop layout from 860px", () => {
    expect(at(860).isMobile).toBe(false);
    expect(at(1280).isMobile).toBe(false);
  });
});

describe("useBreakpoint — the header's compact form", () => {
  it("is compact up to 1139px, from where the desktop layout starts", () => {
    expect(at(860).isCompact).toBe(true);
    expect(at(1139).isCompact).toBe(true);
  });

  it("is the full header from 1140px", () => {
    expect(at(1140).isCompact).toBe(false);
    expect(at(1440).isCompact).toBe(false);
  });
});

describe("useBreakpoint — with no matchMedia", () => {
  it("falls back to the desktop layout and the full header", () => {
    /* A test renderer has no matchMedia at all; the fallbacks are what every
     * component test that does not stub it renders against. */
    vi.stubGlobal("matchMedia", undefined);
    const { result } = renderHook(() => useBreakpoint());

    expect(result.current).toEqual({ isMobile: false, isCompact: false, hasHover: true });
  });
});
