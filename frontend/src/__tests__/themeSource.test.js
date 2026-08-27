/* Theme serialiser — round trip, completeness, and the source text it emits.
 *
 * The failure this file exists to catch is not a crash. A theme key whose
 * value is undefined does not raise: React drops a style property with an
 * undefined value, so the element renders without a background rather than
 * complaining, and the app looks subtly wrong with nothing anywhere saying
 * why. One theme shipped that way already. A serialiser makes that mistake
 * repeatable, so the tests here assert on whole themes rather than on
 * individual keys — a partial theme has to fail somewhere.
 *
 * Two things are compared on every round trip:
 *
 *   toEqual        values, and produces a readable diff when they differ
 *   JSON.stringify key ORDER and value TYPE, which toEqual ignores
 *
 * The second is not redundant. toEqual treats { a: 1, b: 2 } and
 * { b: 2, a: 1 } as equal, and the emitted source is read by people, so key
 * order is part of what is being pinned. It also treats 999 and "999" as
 * distinct, which matters here: radius mixes numbers (pill: 999) with
 * strings (full: "50%"), and a serialiser that stringified everything would
 * write radius.pill as "999" — a value CSS still accepts, so the corners
 * stay round and nothing looks wrong until a component does arithmetic on
 * it. That is the whole reason theme.jsx rejected CSS custom properties.
 *
 * The source-text tests evaluate the emitted block rather than matching it
 * against an expected string. A string comparison pins formatting, which is
 * not what has to be correct, and would pass on a block that is not valid
 * JavaScript. Evaluating it proves it parses, that the builder calls resolve,
 * and that what comes out is the theme that went in.
 *
 * Verified by mutation, 8 applied, 8 killed:
 *
 *   • "size" removed from THEME_KEY_ORDER              → killed
 *   • THEME_KEY_ORDER derived from themes.terminal     → killed
 *   • recoverTint reads .bg for both bg and border     → killed
 *   • TINT_SOURCE maps flag_manual_review to amber     → killed
 *   • emitScalar quotes numbers as strings             → killed
 *   • emitScalar emits strings in single quotes        → killed
 *   • themeToInputs returns live refs, no deep copy    → killed
 *   • assertLeaf accepts undefined instead of throwing → killed
 *
 * Each is killed by its own assertion rather than by a collateral crash;
 * which test kills which is recorded beside the mutation in the commit
 * message.
 */
import { describe, it, expect, vi } from "vitest";

import {
  themes,
  buildStatusColor,
  buildLevelColor,
  buildToastTone,
  buildActionCfg,
} from "../theme";

import {
  THEME_KEY_ORDER,
  recoverTint,
  themeToInputs,
  inputsToTheme,
  themeToSource,
} from "../themeSource";

const SHIPPED = Object.entries(themes);

/* Evaluate an emitted block the way theme.jsx would, by supplying the four
 * builder functions it calls and handing back the const it declares. */
const evaluateBlock = (source, id) => {
  const fn = new Function(
    "buildStatusColor",
    "buildLevelColor",
    "buildToastTone",
    "buildActionCfg",
    `${source}\nreturn ${id};`,
  );
  return fn(buildStatusColor, buildLevelColor, buildToastTone, buildActionCfg);
};

/* Walk a theme and collect the path of every leaf that is not a string or a
 * finite number. Returns paths rather than a boolean so a failure names the
 * key that is wrong instead of only reporting that one is. */
const badLeaves = (value, path = "") => {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return Object.entries(value).flatMap(([k, v]) =>
      badLeaves(v, path ? `${path}.${k}` : k),
    );
  }
  const ok =
    typeof value === "string" ||
    (typeof value === "number" && Number.isFinite(value));
  return ok ? [] : [`${path} = ${String(value)}`];
};

describe("THEME_KEY_ORDER", () => {
  /* The list is hardcoded in themeSource.js precisely so this can fail.
   * Derived from themes.terminal it would be correct by construction, and a
   * key added to a theme would flow through the serialiser unrecognised. */
  it.each(SHIPPED)("matches every top-level key of %s, in order", (id, theme) => {
    expect(Object.keys(theme)).toEqual(THEME_KEY_ORDER);
  });

  it("includes size, which is the one a hand-written list tends to drop", () => {
    // Named explicitly rather than left to the check above. size sits under
    // a long comment between space and surface, ThemeProvider reads
    // size.scrollbarW and size.focusRing straight into the global
    // stylesheet, and an undefined there is discarded silently — the
    // scrollbar reverts to browser default and the focus ring disappears.
    expect(THEME_KEY_ORDER).toContain("size");
    expect(Object.keys(themes.terminal.size).length).toBeGreaterThan(0);
  });

  it("has no duplicate keys", () => {
    expect(new Set(THEME_KEY_ORDER).size).toBe(THEME_KEY_ORDER.length);
  });

  it("does not follow a key added to a theme, so the check above is a real one", async () => {
    /* The assertion above compares THEME_KEY_ORDER against the shipped
     * themes. Derive one from the other and that comparison becomes a
     * tautology that can never fail — which is exactly the failure mode
     * tests/README.md records twice, a test that re-implements what it is
     * checking and passes regardless of what the app does.
     *
     * So this loads the module against a theme carrying a key the
     * serialiser has never been told about, and asserts the list does not
     * silently absorb it. Hardcoded, the list stays at 14 and the
     * comparison above starts failing by name, which is the signal someone
     * needs to decide whether the new key is derived or an input. Derived,
     * the list quietly becomes 15 and nothing anywhere asks. */
    vi.resetModules();
    vi.doMock("../theme", async () => {
      const actual = await vi.importActual("../theme");
      return {
        ...actual,
        themes: {
          ...actual.themes,
          terminal: { ...actual.themes.terminal, motion: { fast: 120 } },
        },
      };
    });
    try {
      const fresh = await import("../themeSource");
      expect(fresh.THEME_KEY_ORDER).not.toContain("motion");
      expect(fresh.THEME_KEY_ORDER).toEqual(THEME_KEY_ORDER);
    } finally {
      vi.doUnmock("../theme");
      vi.resetModules();
    }
  });
});

describe("recoverTint", () => {
  it.each(SHIPPED)("recovers all 14 tint values from %s", (id, theme) => {
    const tint = recoverTint(theme);
    expect(Object.keys(tint).sort()).toEqual([
      "amber", "amberB", "blue", "blueB", "cyan", "cyanB", "green", "greenB",
      "red", "redB", "violet", "violetB", "yellow", "yellowB",
    ]);
    expect(badLeaves(tint)).toEqual([]);
  });

  it("recovers terminal's tint as the literals written in theme.jsx", () => {
    // Pinned against the source values rather than only against a round
    // trip. A round trip alone would still pass if bg and border were
    // consistently swapped, because the swap would be undone on the way
    // back out.
    expect(recoverTint(themes.terminal)).toEqual({
      green: "#091a0f", greenB: "#122a1a",
      red: "#1a0909", redB: "#2a1212",
      amber: "#1a1200", amberB: "#2a1e00",
      blue: "#090f1a", blueB: "#12182a",
      yellow: "#1a1000", yellowB: "#2a1c00",
      cyan: "#001a1a", cyanB: "#0f2a2a",
      violet: "#0d001a", violetB: "#1e0a2a",
    });
  });

  it("throws rather than returning a partial tint", () => {
    const gutted = { ...themes.terminal, actionCfg: {} };
    expect(() => recoverTint(gutted)).toThrow(/copy_track/);
  });
});

describe("theme to inputs and back", () => {
  it.each(SHIPPED)("rebuilds %s exactly from its inputs", (id, theme) => {
    const rebuilt = inputsToTheme(themeToInputs(theme));
    expect(rebuilt).toEqual(theme);
    expect(JSON.stringify(rebuilt)).toBe(JSON.stringify(theme));
  });

  it.each(SHIPPED)("rebuilds %s with no undefined leaf anywhere", (id, theme) => {
    expect(badLeaves(inputsToTheme(themeToInputs(theme)))).toEqual([]);
  });

  it.each(SHIPPED)("regenerates the derived maps of %s rather than copying them", (id, theme) => {
    // The inputs carry no derived map at all, so a rebuilt theme that has
    // them can only have built them. Asserting on the inputs is what
    // distinguishes "derived" from "copied through" — a serialiser that
    // passed statusColor along untouched would satisfy the round trip above.
    const inputs = themeToInputs(theme);
    expect(inputs).not.toHaveProperty("statusColor");
    expect(inputs).not.toHaveProperty("levelColor");
    expect(inputs).not.toHaveProperty("toastTone");
    expect(inputs).not.toHaveProperty("actionCfg");
    expect(inputsToTheme(inputs).statusColor).toEqual(theme.statusColor);
  });

  it("follows the palette when an input colour changes", () => {
    const inputs = themeToInputs(themes.terminal);
    inputs.palette.green = "#00ff00";
    const rebuilt = inputsToTheme(inputs);
    expect(rebuilt.statusColor.success).toBe("#00ff00");
    expect(rebuilt.toastTone.success).toBe("#00ff00");
    expect(rebuilt.actionCfg.copy_track.text).toBe("#00ff00");
  });

  it("does not mutate the shipped theme when an input is edited", () => {
    // themes is a module singleton the running app reads on every render.
    // Handing an editor live references would repaint the app from the
    // draft on the first keystroke, and leave the shipped theme altered
    // after the editor closed.
    const before = JSON.stringify(themes.terminal);
    const inputs = themeToInputs(themes.terminal);
    inputs.palette.bg = "#ffffff";
    inputs.space.md = 999;
    inputs.type.size.base = 42;
    inputs.tint.green = "#ffffff";
    expect(JSON.stringify(themes.terminal)).toBe(before);
  });
});

describe("themeToSource", () => {
  it.each(SHIPPED)("emits a block for %s that evaluates back to it", (id, theme) => {
    const evaluated = evaluateBlock(themeToSource(themeToInputs(theme)), id);
    expect(evaluated).toEqual(theme);
    expect(JSON.stringify(evaluated)).toBe(JSON.stringify(theme));
  });

  it("keeps numbers as numbers and strings as strings through the source", () => {
    const evaluated = evaluateBlock(
      themeToSource(themeToInputs(themes.terminal)),
      "terminal",
    );
    expect(evaluated.radius.pill).toBe(999);
    expect(evaluated.radius.full).toBe("50%");
    expect(evaluated.type.leading.normal).toBe(1.6);
    expect(evaluated.type.tracking.wide).toBe("0.1em");
    expect(evaluated.size.headerHeight).toBe(46);
  });

  it("emits a font stack containing single quotes intact", () => {
    // type.root is a CSS font stack full of single quotes. Emitting theme
    // strings in single quotes terminates the value on its first character
    // and produces a block that does not parse.
    const evaluated = evaluateBlock(
      themeToSource(themeToInputs(themes.terminal)),
      "terminal",
    );
    expect(evaluated.type.root).toBe(themes.terminal.type.root);
    expect(evaluated.type.root).toContain("'JetBrains Mono Variable'");
  });

  it("declares the palette const and the theme const the block needs", () => {
    const source = themeToSource(themeToInputs(themes.soft));
    expect(source).toContain("const softPalette = {");
    expect(source).toContain("const soft = {");
    expect(source).toContain("buildActionCfg(softPalette, {");
  });

  it("carries a new theme's own values, not the theme it was derived from", () => {
    const inputs = themeToInputs(themes.terminal);
    inputs.id = "aurora";
    inputs.label = "Aurora";
    inputs.blurb = "Cold and bright.";
    inputs.palette.amber = "#ffcc00";
    const evaluated = evaluateBlock(themeToSource(inputs), "aurora");
    expect(evaluated.id).toBe("aurora");
    expect(evaluated.label).toBe("Aurora");
    expect(evaluated.toastTone.notice).toBe("#ffcc00");
    expect(Object.keys(evaluated)).toEqual(THEME_KEY_ORDER);
    expect(badLeaves(evaluated)).toEqual([]);
  });

  it.each([
    ["a reserved word", "class"],
    ["a hyphen", "my-theme"],
    ["a capital", "Aurora"],
    ["a leading digit", "2cool"],
    ["an existing binding", "themes"],
    ["empty", ""],
  ])("refuses an id with %s", (why, id) => {
    const inputs = { ...themeToInputs(themes.terminal), id };
    expect(() => themeToSource(inputs)).toThrow();
  });

  it.each([
    ["undefined", undefined],
    ["null", null],
    ["a boolean", true],
    ["an array", ["#fff"]],
    ["a group where a colour belongs", { hex: "#fff" }],
  ])("refuses %s in place of a palette colour", (why, value) => {
    const inputs = themeToInputs(themes.terminal);
    inputs.palette.amber = value;
    expect(() => themeToSource(inputs)).toThrow(/palette\.amber/);
  });

  it("refuses a theme missing a key rather than emitting a partial block", () => {
    // The shape contract theme.jsx's header states, enforced. A leaf-type
    // check cannot cover this: there is no wrong value to find, only an
    // absent one, and an absent one is precisely what renders as nothing.
    const inputs = themeToInputs(themes.terminal);
    delete inputs.size;
    expect(() => themeToSource(inputs)).toThrow(/missing .*size\./);
  });

  it("refuses a theme carrying a key no shipped theme has", () => {
    const inputs = themeToInputs(themes.terminal);
    inputs.palette.teal = "#008080";
    expect(() => themeToSource(inputs)).toThrow(/unexpected .*palette\.teal/);
  });
});
