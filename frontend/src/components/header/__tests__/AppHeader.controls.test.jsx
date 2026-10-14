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
 * handler or give the phone the desktop's text. They pin today's behaviour,
 * including where the two layouts differ: the phone's idle SCAN is ⟳ where
 * the desktop's is ↻, and its titles are its own.
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
 *     idle glyph made the desktop's ↻, and its two titles swapped
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ThemeContext, themes } from "../../../theme";
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
const drawer = () => screen.queryByRole("dialog", { name: "Menu" });
const openDrawer = () => fireEvent.click(button("Open menu"));

describe("AppHeader — what the desktop controls call", () => {
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

    const scan = button("⟳ SCAN");
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
});
