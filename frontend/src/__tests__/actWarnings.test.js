/**
 * The act-warning guard, and that it is in place.
 *
 * Thirty-one tests across five files made React print "not wrapped in
 * act(...)", 230 lines of it, and every one of them passed, so nothing made
 * anyone look. Each is fixed, and src/test/setup.js now fails any test that
 * prints the warning, naming the component. These pin the detector it uses
 * (src/test/actWarnings.js), and that setup.js has actually put it on
 * console.error: a guard written but never installed would let the next
 * warning through with every test green.
 *
 * Verified by mutation: the detector matching nothing, and setup.js not
 * installing the guard, both survived the suite without this file and are
 * killed by it. Each of the five fixes undone in turn fails its own test
 * through the guard.
 */
import { describe, expect, it, vi } from "vitest";

import { createActGuard, describeActWarning, isActWarning } from "../test/actWarnings";
import { actGuard } from "../test/setup";

/** React's warning, as it calls console.error. */
const WARNING = [
  "Warning: An update to %s inside a test was not wrapped in act(...).%s",
  "HistoryPanel",
  "\n    at HistoryPanel (HistoryPanel.jsx:120:31)",
];

describe("the act-warning detector", () => {
  it("recognises React's warning, and names the component", () => {
    expect(isActWarning(WARNING)).toBe(true);
    expect(describeActWarning(WARNING)).toBe("An update to HistoryPanel was not wrapped in act(...)");
  });

  it("leaves other errors alone", () => {
    expect(isActWarning(["Failed to load settings", new Error("boom")])).toBe(false);
    expect(isActWarning([new Error("not wrapped in act(")])).toBe(false);
  });

  it("keeps warnings back for take(), and passes everything else on", () => {
    const original = vi.fn();
    const guard = createActGuard(original);

    guard.error(...WARNING);
    guard.error("Failed to load", 42);

    expect(original.mock.calls).toEqual([["Failed to load", 42]]);
    expect(guard.take()).toEqual(["An update to HistoryPanel was not wrapped in act(...)"]);
    expect(guard.take()).toEqual([]);
  });
});

describe("setup.js", () => {
  it("has put the guard on console.error", () => {
    // The same guard, not a second copy: importing setup.js here must not
    // run it again and stack another guard and afterEach on top.
    expect(console.error).toBe(actGuard.error);
    console.error(...WARNING);

    // Taken here, so this test's own warning does not fail it in afterEach.
    expect(actGuard.take()).toEqual(["An update to HistoryPanel was not wrapped in act(...)"]);
  });
});
