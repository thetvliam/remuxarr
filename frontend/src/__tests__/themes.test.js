/* Invariants every shipped theme has to satisfy, checked across all of them
 * rather than against a named list, so a theme added later is covered the
 * moment it is exported from theme.jsx.
 *
 * These exist because the app shipped only dark themes until now, and a
 * light one turns several values that were previously interchangeable into
 * ones that are not. None of what is checked here fails loudly: an invisible
 * hover state and an unreadable logo both render perfectly happily.
 *
 * Contrast floors on palette.text and friends are deliberately NOT asserted.
 * terminal runs muted at 3.23 and dim at 1.94 against its own background,
 * and soft at 4.03 and 2.47 — both below WCAG AA, both intentional, and
 * pinning a floor here would either fail the two themes this app was built
 * around or be set so low it asserted nothing. What is checked instead is
 * the handful of relationships that are objectively wrong when inverted.
 */
import { describe, it, expect } from "vitest";
import { themes } from "../theme";

const SHIPPED = Object.entries(themes);

/* Parse #rgb, #rrggbb and #rrggbbaa into rgba. The 8-digit form matters:
 * every row-state overlay uses it, and the alpha is the whole mechanism. */
const parse = (hex) => {
  const n = parseInt(hex.slice(1), 16);
  if (hex.length === 9) return [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, (n & 255) / 255];
  if (hex.length === 7) return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  return [((n >> 8) & 15) * 17, ((n >> 4) & 15) * 17, (n & 15) * 17, 1];
};

const luminance = ([r, g, b]) => {
  const f = (v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};

const composite = (fg, bg) => {
  const a = parse(fg);
  const b = parse(bg);
  return [0, 1, 2].map((i) => a[i] * a[3] + b[i] * (1 - a[3]));
};

const contrast = (a, b) => {
  const [hi, lo] = [luminance(parse(a)), luminance(parse(b))].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

describe("every shipped theme", () => {
  it.each(SHIPPED)("%s moves row highlights away from its own background", (id, t) => {
    /* rowHoverBg, zebraBg and rowSelectedBg are translucent overlays that
     * mark a row as hovered, striped or selected. A white overlay does that
     * on a dark background and does nothing at all on a light one — the row
     * composites to very nearly the page colour, so hovering has no visible
     * effect and selection cannot be seen.
     *
     * Nothing errors. This is the whole reason the check exists: the first
     * light theme in this app inherits fifteen surface values from a dark
     * one, and three of them are silently inert unless inverted. */
    const bg = luminance(parse(t.palette.bg));
    const wantLighter = t.colorScheme === "dark";

    for (const key of ["rowHoverBg", "zebraBg", "rowSelectedBg"]) {
      const after = luminance(composite(t.surface[key], t.palette.bg));
      const moved = wantLighter ? after - bg : bg - after;
      expect({ key, moved: moved > 0 }).toEqual({ key, moved: true });
    }
  });

  it.each(SHIPPED)("%s keeps the fallback logo mark legible", (id, t) => {
    /* AppHeader draws surface.logoInk as an R on a palette.amber square when
     * the wordmark SVG fails to load. Amber is a light hue on a dark theme
     * and has to be darkened to stay readable on a light one, so ink that is
     * always black works for the first case and fails for the second. This
     * only ever renders in the degraded path, which is exactly where nobody
     * would notice it going wrong. */
    expect(contrast(t.surface.logoInk, t.palette.amber)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(SHIPPED)("%s distinguishes dim from muted from text", (id, t) => {
    // An ordering, not a floor. The three de-emphasis levels are meaningless
    // if any two collapse, and a generated theme can easily land them close.
    const bg = luminance(parse(t.palette.bg));
    const away = (c) => Math.abs(luminance(parse(c)) - bg);
    expect(away(t.palette.dim)).toBeLessThan(away(t.palette.muted));
    expect(away(t.palette.muted)).toBeLessThan(away(t.palette.text));
  });

  it("ships exactly one light theme, which is the one that needs the light wordmark", () => {
    /* AppHeader picks logo-name-light.svg purely from colorScheme, and that
     * branch had no shipped theme exercising it until dawn existed. */
    const light = SHIPPED.filter(([, t]) => t.colorScheme === "light").map(([id]) => id);
    expect(light).toEqual(["dawn"]);
  });
});
