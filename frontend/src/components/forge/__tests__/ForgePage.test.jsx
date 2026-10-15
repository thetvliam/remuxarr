/**
 * ForgePage — the Candidates and Processed switch on a phone.
 *
 * On a phone the page shows one of its two panels at a time, and the tab bar
 * decides which. These pin that hand-over, which moved onto the shared TabBar
 * along with the dashboard's identical Queue and History switch (see
 * App.test.jsx). Each panel is identified by text only it renders: the
 * candidates search box, and the processed list's empty message.
 *
 * On a wider screen both panels show and there is no tab bar, which is pinned
 * too, since that is the other half of the same condition.
 *
 * Run against the page before the move, the panel assertions passed and only
 * the aria-pressed ones failed, the selection marking being new. Verified by
 * mutation: ForgePage handing TabBar a fixed "candidates", and not passing its
 * setter, both survived the suite without this file and are killed by the
 * swap test here.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ForgePage } from "../ForgePage";
import { ThemeProvider } from "../../../theme";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true, status: 200, json: async () => ({ items: [], total: 0 }),
  })));
});
afterEach(() => { vi.unstubAllGlobals(); });

const renderPage = (isMobile) => render(
  <ThemeProvider>
    <ForgePage api="" forgeRefreshKey={0} active={null} processed={[]}
               onAdd={() => {}} onUndo={() => {}} isMobile={isMobile} />
  </ThemeProvider>,
);

/** Render, and wait for the candidates list to arrive. Without the wait the
 *  load lands after the test, outside act(), which src/test/setup.js fails. */
const renderLoaded = async (isMobile) => {
  renderPage(isMobile);
  await screen.findByText(/^No files with AAC 5\.1 audio found/);
};

const candidatesShown = () => screen.queryByPlaceholderText("Search all candidates by filename…");
const processedShown  = () => screen.queryByText(/^No files processed yet/);
const tab = (name) => screen.getByRole("button", { name, exact: true });

describe("ForgePage — the switch on a phone", () => {
  it("shows Candidates first, and only it", async () => {
    await renderLoaded(true);

    expect(candidatesShown()).toBeTruthy();
    expect(processedShown()).toBeNull();
    expect([tab("CANDIDATES"), tab("PROCESSED")].map(b => b.getAttribute("aria-pressed")))
      .toEqual(["true", "false"]);
  });

  it("swaps to Processed when its tab is chosen, and back", async () => {
    await renderLoaded(true);

    fireEvent.click(tab("PROCESSED"));
    expect(processedShown()).toBeTruthy();
    expect(candidatesShown()).toBeNull();
    expect([tab("CANDIDATES"), tab("PROCESSED")].map(b => b.getAttribute("aria-pressed")))
      .toEqual(["false", "true"]);

    fireEvent.click(tab("CANDIDATES"));
    expect(candidatesShown()).toBeTruthy();
    expect(processedShown()).toBeNull();
    // Coming back mounts the panel again, which loads its list again.
    await screen.findByText(/^No files with AAC 5\.1 audio found/);
  });
});

describe("ForgePage — on a wider screen", () => {
  it("shows both panels and no tab bar", async () => {
    await renderLoaded(false);

    expect(candidatesShown()).toBeTruthy();
    expect(processedShown()).toBeTruthy();
    expect(screen.queryByRole("button", { name: "CANDIDATES", exact: true })).toBeNull();
  });
});
