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

  it("ships at least one light theme, so the light wordmark branch is exercised", () => {
    /* AppHeader picks logo-name-light.svg purely from colorScheme, and no
     * shipped theme reached that branch until dawn existed.
     *
     * This originally asserted the light themes were exactly ["dawn"], which
     * passed for one commit and then failed the moment a second was added.
     * That was pinning a fact rather than the invariant it was named for:
     * what has to stay true is that the branch has a shipped theme behind
     * it, not how many. */
    const light = SHIPPED.filter(([, t]) => t.colorScheme === "light");
    expect(light.length).toBeGreaterThan(0);
  });
});

describe("light variants of a parent theme", () => {
  /* The brief for these was explicit: colours only, everything else left
   * alone. That is checkable rather than a matter of care, so it is checked.
   *
   * It is also the thing most likely to rot. Retuning a parent's spacing and
   * forgetting its light variant leaves two themes that are supposed to be
   * the same layout quietly drifting apart, and nothing about the result
   * looks broken enough to investigate. */
  const VARIANTS = [["paper", "terminal"], ["linen", "soft"]];
  const STRUCTURAL = ["type", "radius", "space", "size"];

  it.each(VARIANTS)("%s keeps every structural value of %s", (child, parent) => {
    for (const group of STRUCTURAL) {
      expect(themes[child][group]).toEqual(themes[parent][group]);
      // toEqual ignores key order; the serialiser's output does not.
      expect(JSON.stringify(themes[child][group]))
      .toBe(JSON.stringify(themes[parent][group]));
    }
  });

  it.each(VARIANTS)("%s changes the colours it is supposed to change", (child, parent) => {
    // The other half of the pair. Without it, a variant that was an exact
    // copy of its parent would satisfy the test above perfectly.
    for (const group of ["palette", "tint", "surface"]) {
      const a = group === "tint"
      ? themes[child].actionCfg : themes[child][group];
      const b = group === "tint"
      ? themes[parent].actionCfg : themes[parent][group];
      expect(JSON.stringify(a)).not.toBe(JSON.stringify(b));
    }
    expect(themes[child].colorScheme).toBe("light");
    expect(themes[parent].colorScheme).toBe("dark");
  });
});
