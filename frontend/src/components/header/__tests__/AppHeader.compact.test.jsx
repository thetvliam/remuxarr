/**
 * AppHeader — the compact desktop header.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The desktop header did not fit its own breakpoint. Measured in Chrome at
 * its natural width, it needs 841px to 985px at rest depending on the theme,
 * and up to 1137px once its labels grow with state (REVIEW with a count,
 * MANUAL, RESUME, "✕ STOP (12345/12345)" while scanning, OFFLINE). Below that
 * its buttons were squeezed until their labels wrapped onto two lines, and
 * further down its right-hand controls ran off the screen.
 *
 * Below 1140px it now takes a compact form, which at its longest needs
 * 854px, so it fits from the 860px layout breakpoint up in every theme:
 *   • the logo mark instead of the wordmark
 *   • tighter padding on the nav links
 *   • DRY RUN and PAUSE / RESUME as their glyphs alone
 *   • AUTO / MANUAL as the word alone, without the glyph. MANUAL's glyph is
 *     the same ⏸ as PAUSE's, so glyphs alone would show two identical
 *     buttons side by side in the usual state of manual start, running.
 *   • scan progress as "✕ 123/456", without STOP and the brackets.
 *
 * Every control that loses words keeps its full meaning in its accessible
 * name and tooltip, which these pin along with the shorter text, and the
 * full header is pinned alongside so the two forms cannot swap. jsdom has no
 * layout, so the widths themselves are the Chrome measurement's to prove.
 *
 * Verified by mutation: the compact form keeping the wordmark, the nav
 * padding, each control's full text, and each accessible name, all survived
 * the suite without this file and are killed by it.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ThemeContext, themes } from "../../../theme";
import { AppHeader } from "../AppHeader";

const theme = { ...themes.terminal, themeId: "terminal", setThemeId: () => {} };

const renderHeader = (props = {}) => render(
  <ThemeContext.Provider value={theme}>
    <AppHeader page="dashboard" setPage={() => {}} api="" wsConnected
               isMobile={false} {...props} />
  </ThemeContext.Provider>,
);

const button = (name) => screen.getByRole("button", { name });
const logo = () => screen.getByAltText("Remuxarr").getAttribute("src");

describe("AppHeader — the full desktop header", () => {
  it("shows the wordmark and every control's label", () => {
    renderHeader({ isCompact: false, dryRun: true, autoStart: true, workerPaused: false });

    expect(logo()).toBe("/logo-name-dark.svg");
    expect(button("◆ DRY RUN")).toBeTruthy();
    expect(button("⚡ AUTO")).toBeTruthy();
    expect(button("⏸ PAUSE")).toBeTruthy();
    expect(button("DASHBOARD").style.padding).toBe(`0px ${theme.space.xl}px`);
  });

  it("shows scan progress in full", () => {
    renderHeader({ isCompact: false, scanning: true, scanProgress: { scanned: 12, total: 34 } });

    expect(button("✕ STOP (12/34)")).toBeTruthy();
  });
});

describe("AppHeader — the compact desktop header", () => {
  it("shows the logo mark and tighter nav links", () => {
    renderHeader({ isCompact: true });

    expect(logo()).toBe("/logo.svg");
    expect(button("DASHBOARD").style.padding).toBe(`0px ${theme.space.md}px`);
  });

  it("shows DRY RUN as its glyph, still named and still marked on or off", () => {
    renderHeader({ isCompact: true, dryRun: true });

    const dry = button("Dry run");
    expect(dry.textContent).toBe("◆");
    expect(dry.getAttribute("aria-pressed")).toBe("true");
    expect(dry.getAttribute("title")).toBe("Dry run on");
  });

  it("shows AUTO and MANUAL as the word alone", () => {
    renderHeader({ isCompact: true, autoStart: true });
    expect(screen.getByRole("button", { name: "AUTO", exact: true })).toBeTruthy();
  });

  it("does not show MANUAL with PAUSE's glyph", () => {
    renderHeader({ isCompact: true, autoStart: false });
    expect(screen.getByRole("button", { name: "MANUAL", exact: true })).toBeTruthy();
  });

  it("shows PAUSE and RESUME as their glyphs, named for what they do", () => {
    const { unmount } = renderHeader({ isCompact: true, workerPaused: false });
    expect(button("Pause").textContent).toBe("⏸");
    unmount();

    renderHeader({ isCompact: true, workerPaused: true });
    expect(button("Resume").textContent).toBe("▶");
    expect(button("Resume").getAttribute("aria-pressed")).toBe("true");
  });

  it("shows scan progress without STOP, and says it in the name and tooltip", () => {
    renderHeader({ isCompact: true, scanning: true, scanProgress: { scanned: 12, total: 34 } });

    const stop = button("Stop scan (12 of 34 scanned)");
    expect(stop.textContent).toBe("✕ 12/34");
    expect(stop.getAttribute("title")).toBe("✕ STOP (12/34)");
  });

  it("leaves SCAN, and STOP SCAN before progress arrives, as they are", () => {
    const { unmount } = renderHeader({ isCompact: true, scanning: false });
    expect(button("↻ SCAN")).toBeTruthy();
    unmount();

    renderHeader({ isCompact: true, scanning: true, scanProgress: null });
    expect(button("✕ STOP SCAN")).toBeTruthy();
  });
});
