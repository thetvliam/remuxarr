/**
 * Btn — its two sizes, and what each does when disabled.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Btn had no test of its own, and nothing elsewhere looked at its style:
 * making a disabled button keep its colour instead of greying survived the
 * whole suite. That mattered less while each caller drew its own look. The
 * Settings section actions then moved onto it as size "md", ten buttons that
 * had each written the same style out by hand, so one edit here now changes
 * all of them, and jsdom has no layout to notice. These pin the style rules
 * themselves; whether the pages still look the same was checked in Chromium
 * when they moved.
 *
 * Values come from the default theme rather than literals, so a theme edit
 * does not break them, and every pair compared differs in that theme.
 *
 * Verified by mutation, 12 applied, 12 killed, each against the whole suite
 * with this file absent first. Ten survived that and are killed here:
 *   • md padding using sm's
 *   • md font at sm's size
 *   • md letter spacing at sm's
 *   • md allowed to wrap
 *   • md allowed to shrink
 *   • md greying when disabled
 *   • sm no longer greying when disabled
 *   • the default size being md
 *   • the not-allowed cursor dropped when disabled
 *   • the not-allowed cursor shown when enabled too
 * Two were already killed by ReviewCard.test.jsx and are left to it:
 * aria-pressed not passed through, and the caller's colour ignored.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Btn } from "../Btn";
import { themes, DEFAULT_THEME_ID } from "../../../theme";

const { palette, space, type } = themes[DEFAULT_THEME_ID];

/** What jsdom reads back for `value` set on `prop`, so colours compare
 *  whichever notation the theme writes them in. */
const css = (prop, value) => {
  const el = document.createElement("div");
  el.style[prop] = typeof value === "number" ? `${value}px` : value;
  return el.style[prop];
};

const button = (props) => {
  render(<Btn label="GO" color={palette.red} onClick={() => {}} {...props} />);
  return screen.getByRole("button", { name: "GO" });
};

describe("Btn — sizes", () => {
  it("is the compact sm size unless told otherwise", () => {
    const b = button({});
    expect(b.style.padding).toBe(`${space.xs}px ${space.lg}px`);
    expect(b.style.fontSize).toBe(css("fontSize", type.size.xs));
    expect(b.style.letterSpacing).toBe(type.tracking.normal);
    expect(b.style.whiteSpace).toBe("");
  });

  it("is the Settings action size at md, which neither wraps nor shrinks", () => {
    const b = button({ size: "md" });
    expect(b.style.padding).toBe(`${space.xs}px ${space.xl}px`);
    expect(b.style.fontSize).toBe(css("fontSize", type.size.sm));
    expect(b.style.letterSpacing).toBe(type.tracking.wide);
    expect(b.style.whiteSpace).toBe("nowrap");
    expect(b.style.flexShrink).toBe("0");
  });
});

describe("Btn — colour and cursor", () => {
  it("greys an sm button, border and label both", () => {
    const b = button({ disabled: true });
    expect(b.disabled).toBe(true);
    expect(b.style.color).toBe(css("color", palette.dim));
    expect(b.style.border).toBe(css("border", `1px solid ${palette.dim}`));
    expect(b.style.cursor).toBe("not-allowed");
  });

  it("leaves an md button in its caller's colour, with the same cursor", () => {
    const b = button({ size: "md", disabled: true });
    expect(b.disabled).toBe(true);
    expect(b.style.color).toBe(css("color", palette.red));
    expect(b.style.border).toBe(css("border", `1px solid ${palette.red}`));
    expect(b.style.cursor).toBe("not-allowed");
  });

  it("uses the caller's colour and the pointer while enabled", () => {
    const b = button({});
    expect(b.style.color).toBe(css("color", palette.red));
    expect(b.style.cursor).toBe("pointer");
  });
});
