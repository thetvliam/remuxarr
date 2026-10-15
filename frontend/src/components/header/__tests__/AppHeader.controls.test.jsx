/**
 * AppHeader — what each control does, and the phone's own controls.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * AppHeader.compact.test.jsx pins the desktop header's labels and accessible
 * names. Nothing pinned which handler a header button calls, and nothing at
 * all looked at the phone: the drawer's DRY RUN, AUTO and PAUSE rows and the
 * SCAN button in its top row. A drawer row calling the wrong handler, or
 * leaving the drawer open, or showing the wrong state's words, survived the
 * whole suite, as did the desktop toggles each calling a sibling's handler.
 *
 * These were written before DRY RUN, AUTO, PAUSE and SCAN were each defined
 * once for both layouts, so that sharing them could not quietly swap a
 * handler or give the phone the desktop's text. They pin what the layouts
 * share and where they still differ: the phone SCAN's titles are its own.
 *
 * Verified by mutation, 17 applied, 17 killed. Every one survived the whole
 * suite before this file existed, and each is killed here by the test
 * written for it:
 *   • each desktop toggle calling a sibling's handler (three)
 *   • the desktop SCAN's start and stop swapped
 *   • each drawer row leaving the drawer open (three)
 *   • each drawer row calling a sibling's handler (three)
 *   • each drawer row showing the other state's words (three)
 *   • the phone SCAN's start and stop swapped, its progress ignored, its
 *     idle glyph made the desktop's ↻ (retired: see SCAN below), and its
 *     two titles swapped
 * When the controls moved to HeaderControls.jsx all seventeen were re-aimed
 * at the new code, each still a defect in one control on one layout, and the
 * same tests killed each.
 *
 * aria-pressed. The drawer rows had none, so a screen reader did not
 * announce them as toggles; they now report it as the bar does. AUTO is
 * pressed while auto-start is on, not while it is highlighted (MANUAL),
 * in both forms. Three mutants, all surviving the suite without these two
 * tests and killed by them: the rows losing aria-pressed (the old
 * behaviour), the rows reporting the highlight, and the bar reporting it.
 *
 * SCAN. The phone's idle arrow was ⟳ where the desktop's is ↻, and the
 * desktop stayed untinted while scanning where the phone and the bar's
 * other lit controls fill in; both now match. The mutant that made the
 * phone's arrow ↻ was retired, since that is now the behaviour, and its
 * inverse took its place. Three mutants, all killed: the phone's arrow
 * back to ⟳ (the old behaviour, which the old test required), and the bar
 * or the phone left untinted while scanning, both of which survived
 * without the tint test.
 *
 * SCAN's look. The phone's label was bold and more tightly spaced than the
 * desktop's. The look is now defined once for both forms, and one test
 * compares it across the layouts, idle and while scanning, leaving out the
 * size, where the forms are meant to differ. Five mutants, all surviving
 * the whole suite without that test and killed by it alone: the phone bold
 * again or tighter again (the old behaviour), the desktop bold or tighter,
 * and the phone's idle colour made its own. The earlier SCAN mutants above
 * were re-run on the shared look, the two tint ones re-aimed at it, and
 * each is still killed by the test written for it.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ALPHA, ThemeContext, alpha, themes } from "../../../theme";
import { AppHeader } from "../AppHeader";

const theme = { ...themes.terminal, themeId: "terminal", setThemeId: () => {} };

/** Handlers that each record their own calls, so a swap shows. */
const handlers = () => ({
  onToggleDryRun: vi.fn(), onToggleAutoStart: vi.fn(), onTogglePause: vi.fn(),
  onTriggerScan: vi.fn(), onCancelScan: vi.fn(),
});
const calls = (h) => Object.fromEntries(Object.entries(h).map(([k, f]) => [k, f.mock.calls.length]));

const renderHeader = (props) => {
  const ui = (p) => (
    <ThemeContext.Provider value={theme}>
      <AppHeader page="dashboard" setPage={() => {}} api="" wsConnected {...p} />
    </ThemeContext.Provider>
  );
  const r = render(ui(props));
  return { rerender: (more) => r.rerender(ui({ ...props, ...more })) };
};

const button = (name) => screen.getByRole("button", { name });

/** What jsdom reads back for `value` set on `prop`, so a colour compares
 *  whichever notation produced it. */
const css = (prop, value) => {
  const el = document.createElement("div");
  el.style[prop] = value;
  return el.style[prop];
};
const drawer = () => screen.queryByRole("dialog", { name: "Menu" });
const openDrawer = () => fireEvent.click(button("Open menu"));

describe("AppHeader — the desktop controls", () => {
  it("sends each toggle to its own handler", () => {
    const h = handlers();
    renderHeader({ isMobile: false, dryRun: false, autoStart: true, workerPaused: false, ...h });

    fireEvent.click(button("◇ DRY RUN"));
    expect(calls(h)).toMatchObject({ onToggleDryRun: 1, onToggleAutoStart: 0, onTogglePause: 0 });
    fireEvent.click(button("⚡ AUTO"));
    expect(calls(h)).toMatchObject({ onToggleDryRun: 1, onToggleAutoStart: 1, onTogglePause: 0 });
    fireEvent.click(button("⏸ PAUSE"));
    expect(calls(h)).toMatchObject({ onToggleDryRun: 1, onToggleAutoStart: 1, onTogglePause: 1 });
  });

  it("marks each toggle pressed or not, AUTO by auto-start rather than its highlight", () => {
    const { rerender } = renderHeader({ isMobile: false, dryRun: false, autoStart: true, workerPaused: false, ...handlers() });
    expect(["◇ DRY RUN", "⚡ AUTO", "⏸ PAUSE"].map(n => button(n).getAttribute("aria-pressed")))
      .toEqual(["false", "true", "false"]);

    rerender({ dryRun: true, autoStart: false, workerPaused: true });
    expect(["◆ DRY RUN", "⏸ MANUAL", "▶ RESUME"].map(n => button(n).getAttribute("aria-pressed")))
      .toEqual(["true", "false", "true"]);
  });

  it("starts a scan when idle, and stops one that is running", () => {
    const h = handlers();
    const { rerender } = renderHeader({ isMobile: false, scanning: false, ...h });

    fireEvent.click(button("↻ SCAN"));
    expect(calls(h)).toMatchObject({ onTriggerScan: 1, onCancelScan: 0 });

    rerender({ scanning: true });
    fireEvent.click(button("✕ STOP SCAN"));
    expect(calls(h)).toMatchObject({ onTriggerScan: 1, onCancelScan: 1 });
  });
});

describe("AppHeader — the phone drawer", () => {
  /** The drawer row whose text mentions `word`; its full text is asserted. */
  const row = (word) => screen.getByRole("button", { name: new RegExp(word) });

  it("says each control's state in words while they are off", () => {
    renderHeader({ isMobile: true, dryRun: false, autoStart: true, workerPaused: false, ...handlers() });
    openDrawer();

    expect(row("DRY RUN").textContent).toBe("◇ DRY RUN  — tap to enable");
    expect(row("AUTO-START").textContent).toBe("⚡ AUTO-START  — tap to disable");
    expect(row("PAUSE").textContent).toBe("⏸ PAUSE  — tap to pause after current job");
  });

  it("says each control's state in words while they are on", () => {
    renderHeader({ isMobile: true, dryRun: true, autoStart: false, workerPaused: true, ...handlers() });
    openDrawer();

    expect(row("DRY RUN").textContent).toBe("◆ DRY RUN  — tap to disable");
    expect(row("MANUAL").textContent).toBe("⏸ MANUAL  — tap to enable auto-start");
    expect(row("RESUME").textContent).toBe("▶ RESUME  — tap to resume processing");
  });

  it("marks each row pressed or not, as the bar does", () => {
    /* AUTO is pressed while auto-start is on, although what it highlights
     * is MANUAL: aria-pressed reports the toggle's own state, not its
     * highlight, the same as the bar. */
    const { rerender } = renderHeader({ isMobile: true, dryRun: false, autoStart: true, workerPaused: false, ...handlers() });
    openDrawer();
    expect(["DRY RUN", "AUTO-START", "PAUSE"].map(w => row(w).getAttribute("aria-pressed")))
      .toEqual(["false", "true", "false"]);

    rerender({ dryRun: true, autoStart: false, workerPaused: true });
    expect(["DRY RUN", "MANUAL", "RESUME"].map(w => row(w).getAttribute("aria-pressed")))
      .toEqual(["true", "false", "true"]);
  });

  it("sends each row to its own handler, and closes the drawer", () => {
    const h = handlers();
    renderHeader({ isMobile: true, dryRun: false, autoStart: true, workerPaused: false, ...h });

    openDrawer();
    fireEvent.click(row("DRY RUN"));
    expect(calls(h)).toMatchObject({ onToggleDryRun: 1, onToggleAutoStart: 0, onTogglePause: 0 });
    expect(drawer()).toBeNull();

    openDrawer();
    fireEvent.click(row("AUTO-START"));
    expect(calls(h)).toMatchObject({ onToggleDryRun: 1, onToggleAutoStart: 1, onTogglePause: 0 });
    expect(drawer()).toBeNull();

    openDrawer();
    fireEvent.click(row("PAUSE"));
    expect(calls(h)).toMatchObject({ onToggleDryRun: 1, onToggleAutoStart: 1, onTogglePause: 1 });
    expect(drawer()).toBeNull();
  });
});

describe("AppHeader — the phone's SCAN", () => {
  it("offers a scan when idle, and starts one", () => {
    const h = handlers();
    renderHeader({ isMobile: true, scanning: false, ...h });

    const scan = button("↻ SCAN");
    expect(scan.title).toBe("Scan library now");
    fireEvent.click(scan);
    expect(calls(h)).toMatchObject({ onTriggerScan: 1, onCancelScan: 0 });
  });

  it("stops a running scan, and shows its progress once that arrives", () => {
    const h = handlers();
    const { rerender } = renderHeader({ isMobile: true, scanning: true, scanProgress: null, ...h });

    const stop = button("✕ STOP");
    expect(stop.title).toBe("Cancel the running scan");
    fireEvent.click(stop);
    expect(calls(h)).toMatchObject({ onTriggerScan: 0, onCancelScan: 1 });

    rerender({ scanProgress: { scanned: 12, total: 34 } });
    expect(button("✕ 12/34")).toBeTruthy();
  });
  it("tints red while a scan runs, on the desktop as on the phone", () => {
    /* The desktop's other lit controls (DRY RUN, MANUAL, RESUME) all fill
     * in when lit; SCAN was the one drawn with a border alone, and only on
     * the desktop. */
    const tint = css("background", alpha(theme.palette.red, ALPHA.low));
    for (const isMobile of [false, true]) {
      const { rerender } = renderHeader({ isMobile, scanning: false, ...handlers() });
      expect(button("↻ SCAN").style.background).toBe("transparent");

      rerender({ scanning: true, scanProgress: { scanned: 12, total: 34 } });
      const stop = isMobile ? button("✕ 12/34") : button("✕ STOP (12/34)");
      expect(stop.style.background).toBe(tint);
      cleanup();
    }
  });

  it("looks the same as the desktop's, idle and while scanning", () => {
    /* SCAN's look is defined once for both forms; the phone's had drifted
     * to a bold, more tightly spaced label. Size is where the forms are
     * meant to differ (padding, margin, and the phone's no-shrink and
     * no-wrap), so only the look is compared. */
    const LOOK = ["fontWeight", "letterSpacing", "fontSize", "fontFamily", "color",
                  "border", "borderRadius", "background", "animation", "cursor"];
    const look = (el) => Object.fromEntries(LOOK.map(p => [p, el.style[p]]));
    const seen = {};
    for (const isMobile of [false, true]) {
      const { rerender } = renderHeader({ isMobile, scanning: false, ...handlers() });
      const idle = look(button("↻ SCAN"));
      rerender({ scanning: true, scanProgress: { scanned: 12, total: 34 } });
      const running = look(isMobile ? button("✕ 12/34") : button("✕ STOP (12/34)"));
      seen[isMobile ? "phone" : "desktop"] = { idle, running };
      cleanup();
    }
    expect(seen.desktop.idle.letterSpacing).not.toBe("");   // a real look, not two empty ones
    expect(seen.phone).toEqual(seen.desktop);
  });
});
