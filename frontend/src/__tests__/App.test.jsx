/**
 * App — the first test of the component itself.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Everything App does is wiring: it reads useAppData and useActions and
 * hands their state and callbacks to the pages and panels. The components on
 * either side are tested, and a mutant that broke the hand-over between them
 * survived, because nothing rendered App. Adding the first App-level test was
 * recorded as a decision to raise rather than take, and was agreed when the
 * phone layout's Queue and History switch moved onto the shared TabBar.
 *
 * It starts with that switch: on a phone the dashboard shows the Queue or the
 * History panel, never both, and the tab bar decides which. Each panel is
 * identified by text only it renders: the empty queue's message (its filter
 * box appears only once there is something to filter), and History's
 * search box.
 *
 * The harness, and why each part is there:
 *   • useWebSocket is replaced, as useAppData's own tests replace it, so no
 *     socket is opened.
 *   • matchMedia answers any max-width query as matching, which is what a
 *     phone answers. Matching on the kind of query rather than its number
 *     keeps this test independent of where the breakpoint sits.
 *   • fetch answers every load with an empty result of the shape that
 *     endpoint returns.
 *
 * Run against today's code before the switch moved onto TabBar, the panel
 * assertions passed and only the aria-pressed ones failed, since marking the
 * selected tab is new: the move kept the behaviour. Verified by mutation:
 * App handing TabBar a fixed "queue" in place of its state, and App not
 * passing its setter, both survived the suite without this file and are
 * killed by the swap test here.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { ThemeProvider } from "../theme";

vi.mock("../hooks/useWebSocket", () => ({ useWebSocket: () => true }));

function emptyAnswer(url) {
  const u = String(url);
  if (u.includes("/api/history/summary")) {
    return { success: 0, failed: 0, failed_only: 0, skipped: 0, dry_run: 0, bytes_saved: 0 };
  }
  if (u.includes("/api/history") || u.includes("/api/forge/candidates")) {
    return { items: [], total: 0 };
  }
  if (u.includes("/api/queue") || u.includes("/api/forge")) return [];
  return {};
}

beforeEach(() => {
  vi.stubGlobal("matchMedia", (q) => ({
    matches: q.includes("max-width"),
    media: q,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  }));
  vi.stubGlobal("fetch", vi.fn(async (url) => ({
    ok: true, status: 200, json: async () => emptyAnswer(url),
  })));
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

const queueShown   = () => screen.queryByText("Queue is empty");
const historyShown = () => screen.queryByPlaceholderText("Search all history by filename…");
const tab = (name) => screen.getByRole("button", { name, exact: true });

describe("App — the dashboard's Queue and History switch on a phone", () => {
  it("shows the Queue panel first, and only it", async () => {
    render(<ThemeProvider><App /></ThemeProvider>);

    expect(await screen.findByText("Queue is empty")).toBeTruthy();
    expect(historyShown()).toBeNull();
    expect([tab("QUEUE"), tab("HISTORY")].map(b => b.getAttribute("aria-pressed")))
      .toEqual(["true", "false"]);
  });

  it("swaps to the History panel when its tab is chosen, and back", async () => {
    render(<ThemeProvider><App /></ThemeProvider>);
    await screen.findByText("Queue is empty");

    fireEvent.click(tab("HISTORY"));
    expect(historyShown()).toBeTruthy();
    expect(queueShown()).toBeNull();
    expect([tab("QUEUE"), tab("HISTORY")].map(b => b.getAttribute("aria-pressed")))
      .toEqual(["false", "true"]);

    fireEvent.click(tab("QUEUE"));
    expect(queueShown()).toBeTruthy();
    expect(historyShown()).toBeNull();
  });
});
