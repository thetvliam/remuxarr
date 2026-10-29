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
 *   • matchMedia answers as a 390px window, through stubViewport
 *     (src/test/viewport.js). These tests once answered any max-width query
 *     as matching, to stay independent of where the breakpoint sits; they
 *     now use the one helper every viewport test shares, at a phone's width
 *     well clear of the boundary. The helper says the pointer can hover,
 *     which a phone's cannot, and nothing here depends on it: hover only
 *     reveals the queue rows' own buttons, and the queue is empty.
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
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { ThemeProvider } from "../theme";
import { stubViewport } from "../test/viewport";

/* The handler is kept so a test can deliver a message, as useAppData's
 * tests do. The mock answers as before for every test that does not. */
const ws = vi.hoisted(() => ({ onMessage: null }));
vi.mock("../hooks/useWebSocket", () => ({
  useWebSocket: (_url, onMessage) => { ws.onMessage = onMessage; return true; },
}));

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
  stubViewport(390);
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

/* The header's compact form, through App. useBreakpoint decides it and the
 * header renders it, and both are tested on their own; what neither would
 * notice is useAppData or App failing to pass it along, which leaves the
 * full header squeezed between 860 and 1140px exactly as before. Unlike the
 * phone tests above, these depend on where the boundaries sit.
 *
 * Verified by mutation: useAppData not returning isCompact, and App not
 * passing it to the header, both survived the suite without this block and
 * are killed by it. */
describe("App — the header at each width", () => {
  const headerLogo = () => document.querySelector("header img").getAttribute("src");

  it("is the phone header below 860px", async () => {
    stubViewport(859);
    render(<ThemeProvider><App /></ThemeProvider>);
    await screen.findByText("Queue is empty");

    expect(screen.getByRole("button", { name: "Open menu" })).toBeTruthy();
  });

  it("is the compact header from 860px to 1139px", async () => {
    stubViewport(1000);
    render(<ThemeProvider><App /></ThemeProvider>);
    await screen.findByText("Queue is empty");

    expect(screen.queryByRole("button", { name: "Open menu" })).toBeNull();
    expect(headerLogo()).toBe("/logo.svg");
    expect(screen.getByRole("button", { name: "Dry run" })).toBeTruthy();
  });

  it("is the full header from 1140px", async () => {
    stubViewport(1140);
    render(<ThemeProvider><App /></ThemeProvider>);
    await screen.findByText("Queue is empty");

    expect(headerLogo()).toMatch(/^\/logo-name-/);
    expect(screen.getByRole("button", { name: /^[◆◇] DRY RUN$/ })).toBeTruthy();
  });
});

/* The Review tab's count. useAppData adds the manual-review items to the
 * language questions waiting, and its own tests pin that sum; nothing
 * checked that it reaches the tab. App passing a zero in its place, or the
 * header's label leaving the count out, each passed every test, and the
 * badge going blank is the regression App's comment at that prop records.
 * Rendered at the full header's width, where the tab is in the nav, with
 * each of the three counts different so that one left out shows.
 *
 * Verified by mutation: those two survived the suite without this test and
 * are killed by it. The sum losing its language half was already killed in
 * useAppData's tests, and the label showing a count of 0 by the scrollbar
 * test below, which finds the tab by its exact name; neither is claimed. */
describe("App — the Review tab's count", () => {
  it("shows the number of things waiting on the Review page", async () => {
    stubViewport(1140);
    vi.stubGlobal("fetch", vi.fn(async (url) => ({
      ok: true, status: 200,
      json: async () => String(url).includes("/api/queue/stats")
        ? { manual_review: 2, language_review: { audio: 3, subtitle: 1 } }
        : emptyAnswer(url),
    })));
    render(<ThemeProvider><App /></ThemeProvider>);
    await screen.findByText("Queue is empty");

    expect(await screen.findByRole("button", { name: "REVIEW (6)" })).toBeTruthy();
  });
});

/* Clear Database and the orphaned-file removal delete rows the dashboard is
 * still showing, and neither endpoint broadcasts. App hands SettingsPage
 * refreshAllPanels as onRecordsRemoved for exactly this, in two places: the
 * Settings page, and the live Settings preview in the theme editor (#themes).
 * Without it the queue is loaded only at start-up and on socket events, so
 * returning to the dashboard showed rows the wipe had deleted. The hand-over
 * inside Settings is pinned in MaintenanceSection.test.jsx; nothing checked
 * that App makes it, and dropping the prop at either site passed every test.
 *
 * Each test queues ep1.mkv, clears the database from one site, goes back to
 * the dashboard and expects the queue empty. The queue endpoint answers with
 * the item until the clear request arrives and with nothing after, as the
 * server would, so the item disappears only if the queue is fetched again.
 *
 * Verified by mutation: the prop dropped at the Settings page, and dropped
 * at the theme editor, each survived the suite without this block and is
 * killed by its own test here and by no other. */
describe("App — records removed in Settings reach the dashboard", () => {
  let cleared;
  let queueLoads;

  beforeEach(() => {
    cleared = false;
    queueLoads = 0;
    // A desktop window, so the header's page links are in reach.
    stubViewport(1280);
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const u = String(url);
      const reply = (body) => ({ ok: true, status: 200, json: async () => body });
      if (u.includes("/api/settings/clear-database")) { cleared = true; return reply({}); }
      if (u.includes("/api/settings/schema")) return reply([]);
      if (/\/api\/queue\/$/.test(u)) {
        queueLoads += 1;
        return reply(cleared ? [] : [{ id: 1, status: "pending", file: { filename: "ep1.mkv" } }]);
      }
      return reply(emptyAnswer(url));
    }));
    localStorage.setItem("remuxarr.settingsCategory", "backup");
  });

  afterEach(() => { window.location.hash = ""; });

  /** Clear Database, confirmed, then back to the dashboard. */
  async function clearThenReturn() {
    fireEvent.click(await screen.findByRole("button", { name: "CLEAR DATABASE" }));
    fireEvent.click(screen.getByRole("button", { name: /CLICK AGAIN TO CONFIRM/ }));
    await waitFor(() => expect(cleared).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: "DASHBOARD", exact: true }));
  }

  it("empties the queue after Clear Database on the Settings page", async () => {
    render(<ThemeProvider><App /></ThemeProvider>);
    expect(await screen.findByText("ep1.mkv")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "SETTINGS", exact: true }));
    await clearThenReturn();

    expect(await screen.findByText("Queue is empty")).toBeTruthy();
    expect(screen.queryByText("ep1.mkv")).toBeNull();
  });

  it("empties the queue after Clear Database in the theme editor's preview", async () => {
    window.location.hash = "#themes";
    render(<ThemeProvider><App /></ThemeProvider>);
    // The dashboard is not on screen here, so wait for the start-up load to
    // have answered with the item: after that, only a refresh can remove it.
    await waitFor(() => expect(queueLoads).toBe(1));
    await act(async () => {});

    await clearThenReturn();

    expect(await screen.findByText("Queue is empty")).toBeTruthy();
    expect(screen.queryByText("ep1.mkv")).toBeNull();
  });
});

/* Settings and Review each scroll in a container App draws around them
 * (PAGE_SCROLLER in App.jsx), which keeps the scrollbar's strip reserved
 * whether or not the page is long enough to scroll. Without it the scrollbar
 * took its width from the page only when it appeared, so moving between a
 * Settings section that scrolls and one that does not moved the page
 * sideways, and Review moved when its list got short. jsdom draws no
 * scrollbars, so these read the property; the movement was measured in
 * Chromium with real scrollbars, before and after.
 *
 * Verified by mutation: the strip dropped from the Settings page's
 * container, from Review's, and from the style they share, each survived the
 * suite without these tests. The first two are each killed by their own test
 * here and by no other, the third by both. The theme editor's preview, which
 * shows the Settings page in a container of its own, is pinned in
 * ThemeEditorPage.test.jsx. */
describe("App — Settings and Review keep the scrollbar's strip", () => {
  beforeEach(() => { stubViewport(1280); });
  // Choosing a page puts it in the address, which outlives the render.
  afterEach(() => { window.location.hash = ""; });

  /** The nearest element around `el` that scrolls. */
  const scrollerAround = (el) => {
    for (let e = el.parentElement; e; e = e.parentElement) {
      if (e.style.overflowY === "auto") return e;
    }
    return null;
  };

  it.each([
    ["SETTINGS", "SAVE CHANGES"],
    ["REVIEW", "MANUAL REVIEW"],
  ])("the %s page scrolls in a container that reserves it", async (link, onThePage) => {
    render(<ThemeProvider><App /></ThemeProvider>);
    await screen.findByText("Queue is empty");

    fireEvent.click(screen.getByRole("button", { name: link, exact: true }));
    const scroller = scrollerAround(await screen.findByText(onThePage));
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });

    expect(scroller.style.scrollbarGutter).toBe("stable");
  });
});

/* The header's PAUSE button follows the worker when the worker pauses
 * itself. Switching to MANUAL pauses it (worker._loop acts on the setting
 * changing), and so does the end of a run started with Resume while
 * auto-start is off, which arrives as worker_paused. useActions and
 * useAppData each pin their half on spies or on the hook's return; these
 * pin that App carries it to the button, at the full header's width where
 * both controls are labelled. The server is modelled as the backend now
 * behaves: once auto-start is switched off, the status reports paused.
 *
 * Verified by mutation: useAppData handing useActions a setWorkerPaused that
 * does nothing, and App handing the header a fixed workerPaused, each
 * survived the suite without this block. */
describe("App — the header's PAUSE button follows the worker", () => {
  let autoStart;

  beforeEach(() => {
    stubViewport(1140);
    autoStart = true;
    vi.stubGlobal("fetch", vi.fn(async (url, opts = {}) => {
      const u = String(url);
      const reply = (body) => ({ ok: true, status: 200, json: async () => body });
      if (u.includes("/api/settings/auto_start_jobs")) {
        if (opts.method === "PUT") autoStart = JSON.parse(opts.body).value;
        return reply({ key: "auto_start_jobs", value: autoStart });
      }
      if (u.includes("/api/worker/status")) return reply({ paused: !autoStart, active_jobs: 0 });
      return reply(emptyAnswer(url));
    }));
  });

  const control = (name) => screen.getByRole("button", { name });

  it("reads paused as soon as auto-start is switched to MANUAL", async () => {
    render(<ThemeProvider><App /></ThemeProvider>);
    await screen.findByText("Queue is empty");
    expect(control("⏸ PAUSE")).toBeTruthy();

    await act(async () => { fireEvent.click(control("⚡ AUTO")); });

    expect(await screen.findByRole("button", { name: "⏸ MANUAL" })).toBeTruthy();
    expect(control("▶ RESUME")).toBeTruthy();
  });

  it("reads paused when the worker says it has paused", async () => {
    render(<ThemeProvider><App /></ThemeProvider>);
    await screen.findByText("Queue is empty");
    expect(control("⏸ PAUSE")).toBeTruthy();

    await act(async () => { ws.onMessage({ event: "worker_paused" }); });

    expect(control("▶ RESUME")).toBeTruthy();
  });
});

/* Ask again, through App. The modal shows the button when App passes a
 * handler, and App passes one only when the details say forgetting would
 * change something for the file (file.ask_again_changes, which only the
 * details response carries) and not while its job runs. The row opens the
 * modal at once and the detail fetch then enriches it, so a job that started
 * in between arrives as "processing" there.
 *
 * Verified by mutation, 3 applied, 3 killed, all three surviving the whole
 * suite first: offered without answers, offered while processing, and the
 * handler not reaching askAgainItem. App reading the old row flag,
 * has_answers, in place of ask_again_changes is killed here too; it was
 * already killed by these tests once their fixtures carried the new flag. */
describe("App — Ask again in the details window", () => {
  const ROW = {
    id: 5, status: "pending", reason: "Remove 1 subtitle track", planned_actions: [],
    file: { id: 1, filename: "Ep1.mkv", path: "/tv/Show/Ep1.mkv", ask_again_changes: true },
  };

  function serve(row, detail = row) {
    vi.stubGlobal("fetch", vi.fn(async (url, opts = {}) => {
      const u = String(url);
      let body;
      if (u.endsWith("/api/queue/5/ask-again")) body = { ...row, status: "manual_review" };
      else if (u.endsWith("/api/queue/5")) body = detail;
      else if (u.endsWith("/api/queue/")) body = [row];
      else body = emptyAnswer(u);
      return { ok: true, status: 200, json: async () => body, method: opts.method };
    }));
  }

  const open = async () => {
    render(<ThemeProvider><App /></ThemeProvider>);
    fireEvent.click(await screen.findByTitle("Show details"));
    await screen.findByRole("dialog");
    // Let the detail fetch land and enrich the modal.
    await act(async () => {});
  };

  it("forgets the answers in two clicks", async () => {
    serve(ROW);
    await open();

    fireEvent.click(screen.getByRole("button", { name: "↺ ASK AGAIN" }));
    fireEvent.click(screen.getByRole("button", { name: "CLICK AGAIN TO CONFIRM" }));

    await waitFor(() => expect(fetch.mock.calls.some(([url, opts]) =>
      String(url).endsWith("/api/queue/5/ask-again") && opts?.method === "POST")).toBe(true));
  });

  /* Rows do not carry the flag: it takes two runs of the decision engine,
   * so only the details response has it, and the button waits for it. */
  const BARE = { id: 1, filename: "Ep1.mkv", path: "/tv/Show/Ep1.mkv" };

  it("appears once the details say asking again would change something", async () => {
    serve({ ...ROW, file: BARE }, ROW);
    await open();

    expect(await screen.findByRole("button", { name: "↺ ASK AGAIN" })).toBeTruthy();
  });

  it("is not offered when the details say nothing would change", async () => {
    serve({ ...ROW, file: BARE }, { ...ROW, file: { ...BARE, ask_again_changes: false } });
    await open();

    expect(screen.queryByRole("button", { name: /ASK AGAIN/ })).toBeNull();
  });

  it("is not offered for a file with nothing stored", async () => {
    const row = { ...ROW, file: { ...ROW.file, ask_again_changes: false } };
    serve(row);
    await open();

    expect(screen.queryByRole("button", { name: /ASK AGAIN/ })).toBeNull();
  });

  it("is not offered once the details say the file's job is running", async () => {
    serve(ROW, { ...ROW, status: "processing" });
    await open();

    await waitFor(() => expect(screen.queryByRole("button", { name: /ASK AGAIN/ })).toBeNull());
  });
});
