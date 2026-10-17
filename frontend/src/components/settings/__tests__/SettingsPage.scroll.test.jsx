/**
 * SettingsPage — switching category, and scrolling a long one.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Every category is drawn in the same scrolling area, App's container around
 * this page, so picking another kept the scroll position: from the bottom of
 * a long category the next one opened partway down, or at its own bottom.
 * The page now puts the scroll back to the top whenever the category
 * changes, from the sidebar or from the phone menu.
 *
 * Scrolling also moved the sidebar. It was sticky at 0 inside the page's top
 * padding, so the first 24px of scrolling (30px in soft and linen) carried
 * it up to the header before it stopped. It sticks at the padding's offset
 * now, which is where it rests. The save bar slid the same way, and so did
 * the phone menu, by the phone's 16px (20px) padding. Their sticky blocks now
 * reach up over the padding (a negative margin) and fill it with the page's
 * background (padding of the same size), so they rest where they did and
 * stay there, with what scrolls up passing under the band.
 *
 * jsdom has no layout and does not scroll, but it keeps a scrollTop that is
 * set and reports the overflow it was given, which is all the reset needs.
 * For the sticky offsets it can only read back the styles, so those tests
 * pin the offsets against the page's own padding rather than numbers. The
 * movement itself was measured in Chromium, in all six themes, on a desktop
 * and a phone: the sidebar, the save bar and the phone menu each stayed at
 * one position from the top of the page to the bottom, and every switch
 * made from the bottom of a page opened at the top.
 *
 * Verified by mutation, 5 applied, 5 killed, every one surviving the whole
 * suite without this file:
 *   • the scroll reset removed, and the reset run only when the page first
 *     loads rather than on every switch (each killed by both switching tests)
 *   • the sidebar sticking at 0 again
 *   • the save bar's band removed, and the phone menu's band removed
 * Each of the last three is killed by its own test here and by nothing else.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../SettingsPage";
import { ThemeProvider, useTheme } from "../../../theme";
import { stubViewport } from "../../../test/viewport";

const SCHEMA = [
  { key: "sonarr_url", label: "Sonarr URL", type: "string", group: "Sonarr" },
  { key: "radarr_url", label: "Radarr URL", type: "string", group: "Radarr" },
];
const VALUES = { sonarr_url: "", radarr_url: "" };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    const u = String(url);
    const body =
      u.includes("/api/settings/schema") ? SCHEMA
      : u.match(/\/api\/settings\/?$/) ? VALUES
      : u.includes("/api/logs") ? { records: [] }
      : { value: null, count: 0 };
    return { ok: true, status: 200, json: async () => body };
  }));
});

/** The theme the page is drawn with, read from inside the provider. */
let theme;
const Probe = () => { theme = useTheme(); return null; };

/** What jsdom reads back for `value` set on `prop`. */
const css = (prop, value) => {
  const el = document.createElement("div");
  el.style[prop] = value;
  return el.style[prop];
};

/** Let every load the page started finish inside act. */
const settle = () => act(async () => { await new Promise(r => setTimeout(r, 0)); });

/** Integrations, drawn as App draws it: inside a container that scrolls,
 * at a phone's width or a desktop's. Returns that container. */
const renderPage = async ({ phone = false } = {}) => {
  stubViewport(phone ? 390 : 1280);
  localStorage.setItem("remuxarr.settingsCategory", "integrations");
  render(
    <ThemeProvider>
      <Probe />
      <div data-testid="scroller" style={{ flex: 1, overflowY: "auto" }}>
        <SettingsPage api="" toast={() => {}} isMobile={phone} />
      </div>
    </ThemeProvider>,
  );
  await settle();
  await screen.findByText("Sonarr URL");
  return screen.getByTestId("scroller");
};

/** The page's outermost element, whose top padding the sticky parts allow for. */
const pageRoot = () => screen.getByTestId("scroller").firstElementChild;

/** The nearest element above `el` that is sticky. */
const stickyAbove = (el) => {
  for (let e = el; e; e = e.parentElement) if (e.style.position === "sticky") return e;
  return null;
};

describe("SettingsPage — switching category", () => {
  it("opens the category picked from the sidebar at its top", async () => {
    const scroller = await renderPage();
    scroller.scrollTop = 500;
    expect(scroller.scrollTop).toBe(500);

    fireEvent.click(screen.getByRole("button", { name: "Maintenance & Logs" }));
    await settle();

    expect(screen.getByRole("heading", { level: 2, name: "MAINTENANCE" })).toBeInTheDocument();
    expect(scroller.scrollTop).toBe(0);
  });

  it("opens the category picked from the phone menu at its top", async () => {
    const scroller = await renderPage({ phone: true });
    scroller.scrollTop = 500;
    expect(scroller.scrollTop).toBe(500);

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "maintenance" } });
    await settle();

    expect(screen.getByRole("heading", { level: 2, name: "MAINTENANCE" })).toBeInTheDocument();
    expect(scroller.scrollTop).toBe(0);
  });
});

describe("SettingsPage — what stays put while the page scrolls", () => {
  it("the sidebar sticks where it rests, below the page's top padding", async () => {
    await renderPage();
    const nav = screen.getByRole("navigation");
    const padding = pageRoot().style.paddingTop;

    expect(padding).toBe(`${theme.space.huge}px`);
    expect(nav.style.position).toBe("sticky");
    expect(nav.style.top).toBe(padding);
  });

  it("the save bar's band covers the page's top padding, in the page's background", async () => {
    await renderPage();
    const band = stickyAbove(screen.getByRole("button", { name: "SAVE CHANGES" }));
    const padding = pageRoot().style.paddingTop;

    expect(band.style.top).toBe("0px");
    expect([band.style.marginTop, band.style.paddingTop]).toEqual([`-${padding}`, padding]);
    expect(band.style.background).toBe(css("background", theme.palette.bg));
  });

  it("on a phone, the menu's band covers the page's top padding, in the page's background", async () => {
    await renderPage({ phone: true });
    const band = stickyAbove(screen.getByRole("combobox"));
    const padding = pageRoot().style.paddingTop;

    expect(padding).toBe(`${theme.space.xl}px`);
    expect(band.style.top).toBe("0px");
    expect([band.style.marginTop, band.style.paddingTop]).toEqual([`-${padding}`, padding]);
    expect(band.style.background).toBe(css("background", theme.palette.bg));
  });
});
