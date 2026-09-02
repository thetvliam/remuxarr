/**
 * useAppData — routing, toasts, and history invalidation.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * useAppData is the single source of truth for the whole app and had no
 * tests: 8 mutations, 8 survivors. Three of its behaviours are documented
 * bug fixes whose incident notes live in the source and nowhere else:
 *
 *   toast tone      — the value was stored under `color` while Toasts.jsx read
 *                     `tone`, so every toast silently fell back to the accent
 *                     colour and a failed job looked like a successful save.
 *                     A lookup miss returns a real colour, so nothing failed
 *                     loudly.
 *
 *   setPage guard   — clicking the tab you were already on pushed a duplicate
 *                     history entry, so Back needed one press per click before
 *                     doing anything visible.
 *
 *   invalidateHistory — the raw setHistoryRefreshKey incantation was written
 *                     out by hand at every call site and four sites that
 *                     needed it did not have it. The status argument is what
 *                     lets an unrelated tab skip the refetch, so BOTH halves
 *                     matter: the key must change identity, and the status
 *                     must be carried through.
 *
 * useWebSocket is mocked out — this file is about the hook's own state
 * transitions, not its transport.
 */
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/* useAppData opens a WebSocket on mount. Stub it before importing the hook:
 *the transport has its own surface and is not what these tests are about. */
/* The handler itself is captured so the message cases can be driven
 * directly. vi.hoisted is required: a plain const would still be in its
 * temporal dead zone when vi.mock's factory is hoisted above the imports. */
const ws = vi.hoisted(() => ({ onMessage: null }));
vi.mock("../useWebSocket", () => ({
  useWebSocket: (_url, onMessage) => { ws.onMessage = onMessage; return true; },
}));

const { useAppData } = await import("../useAppData");

/* ── Harness ────────────────────────────────────────────────────────────── */

beforeEach(() => {
  // Every mount calls fetchAll, which fans out over several endpoints.
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true,
    json: async () => ({ value: false, items: [], total: 0 }),
  })));
  // replaceState, not `location.hash = ""` — the latter queues an ASYNC
  // popstate that lands mid-mount and drags the page back to "dashboard".
  window.history.replaceState(null, "", "/");
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

/* Assigning window.location.hash fires a popstate whose event.state is null,
 * which the hook's popstate handler resolves to "dashboard" regardless of the
 * URL (see the routing finding in the Phase 3 report). replaceState sets the
 * fragment WITHOUT firing popstate, which is what a real page load looks
 * like — the fragment is already there and no navigation event has occurred. */
function setHash(hash) {
  window.history.replaceState(null, "", hash);
}

/** Mount the hook and let its on-mount effects settle. */
async function mount() {
  const rendered = renderHook(() => useAppData());
  await act(async () => { await Promise.resolve(); });
  return rendered;
}

/* ── Routing: hash parsing ──────────────────────────────────────────────── */

describe("useAppData — initial page from hash", () => {
  it("lands on the page named in the hash", async () => {
    setHash("#settings");
    const { result } = await mount();
    expect(result.current.page).toBe("settings");
  });

  it("falls back to dashboard for an unknown hash", async () => {
    // A stale bookmark or a typo must not leave the app rendering nothing:
    // App.jsx switches on `page` and has no default branch.
    setHash("#nonsense");
    const { result } = await mount();
    expect(result.current.page).toBe("dashboard");
  });

  it("lands on the developer theme editor when its fragment is used", async () => {
    // #themes is absent from AppHeader's NAV_ITEMS by design, so this set is
    // the only thing that makes the route reachable at all. Drop the entry
    // and the editor does not 404 or warn — it silently resolves to the
    // dashboard, which looks exactly like the route never existing.
    setHash("#themes");
    const { result } = await mount();
    expect(result.current.page).toBe("themes");
  });

  it("falls back to dashboard when there is no hash at all", async () => {
    const { result } = await mount();
    expect(result.current.page).toBe("dashboard");
  });
});

/* ── Routing: setPage ───────────────────────────────────────────────────── */

describe("useAppData — setPage", () => {
  it("navigating pushes exactly one history entry", async () => {
    const push = vi.spyOn(window.history, "pushState");
    const { result } = await mount();
    push.mockClear();

    act(() => { result.current.setPage("settings"); });

    expect(result.current.page).toBe("settings");
    expect(push).toHaveBeenCalledTimes(1);
  });

  it("re-selecting the current page pushes nothing", async () => {
    // Otherwise Back needs one press per click before it does anything
    // visible, and the button reads as broken rather than slow.
    const push = vi.spyOn(window.history, "pushState");
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });
    push.mockClear();

    act(() => { result.current.setPage("settings"); });
    act(() => { result.current.setPage("settings"); });

    expect(push).not.toHaveBeenCalled();
    expect(result.current.page).toBe("settings");
  });
});

/* ── Routing: setModal ──────────────────────────────────────────────────── */

describe("useAppData — setModal", () => {
  it("closing when nothing is open does not touch history", async () => {
    // A spurious history.back() navigates the app away from the current page.
    const back = vi.spyOn(window.history, "back").mockImplementation(() => {});
    const { result } = await mount();
    back.mockClear();

    act(() => { result.current.setModal(null); });

    expect(back).not.toHaveBeenCalled();
  });

  it("closing an open modal steps history back once", async () => {
    const back = vi.spyOn(window.history, "back").mockImplementation(() => {});
    const { result } = await mount();
    act(() => { result.current.setModal({ id: 1 }); });
    back.mockClear();

    act(() => { result.current.setModal(null); });

    expect(result.current.modal).toBeNull();
    expect(back).toHaveBeenCalledTimes(1);
  });
});

/* ── Toasts ─────────────────────────────────────────────────────────────── */

describe("useAppData — toasts", () => {
  it("stores the tone under the key Toasts.jsx reads", async () => {
    // Stored as `color` instead, every toast fell back to the accent colour
    // and a failure was indistinguishable from a success.
    const { result } = await mount();

    act(() => { result.current.toast("Job failed", "bad"); });

    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0]).toMatchObject({ msg: "Job failed", tone: "bad" });
    expect(result.current.toasts[0].tone).toBe("bad");
  });

  it("keeps at most the last eight toasts", async () => {
    const { result } = await mount();

    act(() => {
      for (let i = 0; i < 12; i++) result.current.toast(`msg ${i}`, "info");
    });

    expect(result.current.toasts).toHaveLength(8);
    expect(result.current.toasts[0].msg).toBe("msg 4");
    expect(result.current.toasts[7].msg).toBe("msg 11");
  });

  it("a toast dismisses itself, leaving the others alone", async () => {
    const { result } = await mount();

    act(() => { result.current.toast("first", "info"); });
    // Space the toasts apart so only the first one's timer elapses.
    act(() => { vi.advanceTimersByTime(2000); });
    act(() => { result.current.toast("second", "info"); });
    expect(result.current.toasts).toHaveLength(2);

    act(() => { vi.advanceTimersByTime(3100); });   // first hits 5s, second at 3.1s

    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].msg).toBe("second");
  });
});

/* ── History invalidation ───────────────────────────────────────────────── */

describe("useAppData — invalidateHistory", () => {
  it("advances the key so consumers see a new object identity", async () => {
    // useHistoryData compares refreshKey BY REFERENCE in its dependency
    // array. A key that does not change identity produces no refetch at all.
    const { result } = await mount();
    const before = result.current.historyRefreshKey;

    act(() => { result.current.invalidateHistory("failed"); });

    const after = result.current.historyRefreshKey;
    expect(after).not.toBe(before);
    expect(after.key).toBe(before.key + 1);
  });

  it("carries the status through, so unrelated tabs can skip the refetch", async () => {
    const { result } = await mount();

    act(() => { result.current.invalidateHistory("failed"); });

    expect(result.current.historyRefreshKey.status).toBe("failed");
  });

  it("defaults to a null status, which refreshes every tab", async () => {
    // Call sites that cannot know the item's status must fall back to the
    // unfiltered form rather than guessing one.
    const { result } = await mount();

    act(() => { result.current.invalidateHistory(); });

    expect(result.current.historyRefreshKey.status).toBeNull();
  });

  it("repeated invalidations keep advancing the key", async () => {
    const { result } = await mount();
    const start = result.current.historyRefreshKey.key;

    act(() => { result.current.invalidateHistory("failed"); });
    act(() => { result.current.invalidateHistory("success"); });

    expect(result.current.historyRefreshKey.key).toBe(start + 2);
    expect(result.current.historyRefreshKey.status).toBe("success");
  });
});

/* ── Routing: popstate ──────────────────────────────────────────────────── */

describe("useAppData — popstate", () => {
  it("back to a page entry restores that page", async () => {
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });

    await act(async () => {
      window.dispatchEvent(new PopStateEvent("popstate", {
        state: { page: "review", modal: false },
      }));
    });

    expect(result.current.page).toBe("review");
  });

  it("a popstate carrying an invalid page falls back to dashboard", async () => {
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });

    await act(async () => {
      window.dispatchEvent(new PopStateEvent("popstate", {
        state: { page: "nonsense", modal: false },
      }));
    });

    expect(result.current.page).toBe("dashboard");
  });

  it("a stateless popstate honours the URL instead of forcing dashboard", async () => {
    /* Was marked it.fails while the bug was live — it asserted the correct
     *    behaviour and therefore passed only while the handler was wrong.
     *
     *    A history entry created by a hash change — a manually edited fragment,
     *    or an in-page anchor — carries no state object. The handler read only
     *    event.state, resolved the missing page to "dashboard", and navigated
     *    the app away while the URL still said #settings. State and URL then
     *    disagreed and every later Back press compounded it.
     *
     *    Fixed by falling back to _pageFromHash(), which already existed and
     *    does exactly this on initial load. */
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });
    setHash("#settings");

    await act(async () => {
      window.dispatchEvent(new PopStateEvent("popstate", { state: null }));
    });

    expect(result.current.page).toBe("settings");
  });

  it("a stateless popstate on an unknown fragment still lands on dashboard", async () => {
    /* The fallback must stay a fallback: _pageFromHash validates against
     *    VALID_PAGES, so a stale bookmark or typo resolves to dashboard rather
     *    than leaving App.jsx switching on a page it has no branch for. */
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });
    setHash("#nonsense");

    await act(async () => {
      window.dispatchEvent(new PopStateEvent("popstate", { state: null }));
    });

    expect(result.current.page).toBe("dashboard");
  });
});


/* ── Navigation guard ────────────────────────────────────────────────────── */

/**
 * App.jsx can refuse a nav tab click by simply not calling setPage. Back
 * offers no such point: by the time popstate fires the entry is already
 * popped and the URL already updated, so refusing means putting it back.
 *
 * That gap was live. The unsaved-changes prompt covered tab clicks only, and
 * the beforeunload handler it was said to be paired with fires on document
 * unload — a refresh or a tab close — which hash and pushState routing never
 * trigger. Pressing Back on a dirty Settings page therefore discarded the
 * edits silently, and on Android that is the primary way out of a page.
 */
describe("useAppData — navigation guard", () => {
  /* A real Back updates the URL and THEN fires popstate. Dispatching the
   *  event alone leaves the address bar on the page being left, which would
   *  make any assertion about the guard restoring the URL pass whether or not
   *  it restores anything. replaceState rather than pushState: the browser is
   *  moving between entries that already exist, not adding one. */
  const back = (page = "dashboard") => act(() => {
    window.history.replaceState({ page, modal: false }, "", `#${page}`);
    window.dispatchEvent(new PopStateEvent("popstate", {
      state: { page, modal: false },
    }));
  });

  it("a guard that refuses leaves the page where it was", async () => {
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });
    act(() => { result.current.registerNavGuard(() => true); });

    await back();

    expect(result.current.page).toBe("settings");
  });

  it("restores the URL it had already left, so the two cannot drift", async () => {
    // Refusing in state alone leaves the address bar reading #dashboard on a
    // page still showing Settings, and the next Back compounds it.
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });
    act(() => { result.current.registerNavGuard(() => true); });

    await back();

    expect(window.location.hash).toBe("#settings");
    expect(window.history.state).toEqual({ page: "settings", modal: false });
  });

  it("is told where the navigation was heading", async () => {
    // The predicate takes a target because the guard only blocks leaving:
    // a Back that lands on Settings again is not a departure.
    const seen = [];
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });
    act(() => { result.current.registerNavGuard((t) => { seen.push(t); return true; }); });

    await back("review");

    expect(seen).toEqual(["review"]);
  });

  it("a guard that permits navigates normally", async () => {
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });
    act(() => { result.current.registerNavGuard(() => false); });

    await back("review");

    expect(result.current.page).toBe("review");
  });

  it("navigates normally when no guard is registered", async () => {
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });

    await back("review");

    expect(result.current.page).toBe("review");
  });

  /* history.back() is asynchronous in jsdom, and this file already mocks it
   *  rather than waiting on a real navigation (see setModal above). The
   *  popstate is then dispatched by hand to stand in for the event the browser
   *  fires once the entry is popped — which is the part under test here. */
  it("leaveGuarded goes through, rather than being refused again", async () => {
    // The guard is still registered and still says no — the user has said to
    // go anyway, so this one Back has to be exempt or the prompt is a trap.
    const backSpy = vi.spyOn(window.history, "back").mockImplementation(() => {});
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });
    act(() => { result.current.registerNavGuard(() => true); });
    await back();

    act(() => { result.current.leaveGuarded(); });
    expect(backSpy).toHaveBeenCalledTimes(1);
    await back();

    expect(result.current.page).toBe("dashboard");
  });

  it("leaveGuarded re-issues Back rather than pushing, leaving no dead entry", async () => {
    /* The entry the user wanted is still behind the one pushed to cancel, so
     *    going back reaches it AND removes that pushed entry. Pushing the target
     *    instead would strand two dead Settings entries to press through. */
    const backSpy = vi.spyOn(window.history, "back").mockImplementation(() => {});
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });
    act(() => { result.current.registerNavGuard(() => true); });
    await back();

    const pushSpy = vi.spyOn(window.history, "pushState");
    act(() => { result.current.leaveGuarded(); });

    expect(backSpy).toHaveBeenCalledTimes(1);
    expect(pushSpy).not.toHaveBeenCalled();
  });

  it("the exemption is one-shot, so a later Back is guarded again", async () => {
    vi.spyOn(window.history, "back").mockImplementation(() => {});
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });
    act(() => { result.current.registerNavGuard(() => true); });
    await back();
    act(() => { result.current.leaveGuarded(); });
    await back();

    // Back on some later page, with the guard still refusing.
    act(() => { result.current.setPage("settings"); });
    await back();

    expect(result.current.page).toBe("settings");
  });

  it("unregistering restores unguarded navigation", async () => {
    const { result } = await mount();
    act(() => { result.current.setPage("settings"); });
    let unregister;
    act(() => { unregister = result.current.registerNavGuard(() => true); });
    act(() => { unregister(); });

    await back("review");

    expect(result.current.page).toBe("review");
  });
});


/* ── refreshAllPanels ────────────────────────────────────────────────────── */

/**
 * Clearing the database empties nine tables spanning five separate refresh
 * channels. The endpoint broadcasts nothing and DangerZone is reached from
 * Settings, where the dashboard panels are unmounted, so nothing told them:
 * returning to the dashboard left the queue listing rows the wipe had already
 * deleted, because pendingQueue lives in this hook and survives the page
 * switch.
 *
 * Two of the keys this bumps are not exposed from the hook at all, which is
 * why this is a named helper rather than a caller assembling the same set —
 * the same reasoning as invalidateHistory, whose incantation four call sites
 * had already got wrong.
 */
describe("useAppData — refreshAllPanels", () => {
  it("marks history stale for every tab, not just one", async () => {
    const { result } = await mount();
    const before = result.current.historyRefreshKey;

    act(() => { result.current.refreshAllPanels(); });

    expect(result.current.historyRefreshKey.key).toBe(before.key + 1);
    // null, not a specific status: a wipe touches every tab, so none of them
    // may decide this refresh is unrelated and skip it.
    expect(result.current.historyRefreshKey.status).toBeNull();
  });

  it("bumps the review key, so the language sections refetch", async () => {
    const { result } = await mount();
    const before = result.current.reviewRefreshKey;

    act(() => { result.current.refreshAllPanels(); });

    expect(result.current.reviewRefreshKey).toBe(before + 1);
  });

  it("bumps the revert key, since revert points are wiped too", async () => {
    const { result } = await mount();
    const before = result.current.revertRefreshKey;

    act(() => { result.current.refreshAllPanels(); });

    expect(result.current.revertRefreshKey).toBe(before + 1);
  });

  it("bumps the forge key, since forge jobs are wiped too", async () => {
    // The wider half: scan_completed and cleanup_completed refresh neither
    // forge nor revert, so copying either of them would have missed both.
    const { result } = await mount();
    const before = result.current.forgeRefreshKey;

    act(() => { result.current.refreshAllPanels(); });

    expect(result.current.forgeRefreshKey).toBe(before + 1);
  });

  it("refetches the live lists, including the forge ones", async () => {
    const { result } = await mount();
    fetch.mockClear();

    await act(async () => { result.current.refreshAllPanels(); });

    const urls = fetch.mock.calls.map(c => String(c[0]));
    expect(urls.some(u => u.includes("/api/queue"))).toBe(true);
    expect(urls.some(u => u.includes("/api/forge/active"))).toBe(true);
  });
});


/* ── forge_job_completed ─────────────────────────────────────────────────── */

/**
 * The mirror of job_completed's ordering rule, whose comment reasons at
 * length that state updates must not sit behind a cosmetic string operation
 * that can throw — useWebSocket invokes this callback inside a bare catch, so
 * a throw takes the whole branch with it silently.
 *
 * forge_job_completed guarded msg.status the way that comment credits it for,
 * but ran the toast FIRST and left msg.error unguarded, so the lesson had
 * been drawn and only half applied.
 */
describe("useAppData — forge_job_completed", () => {
  const complete = (msg) => act(() => {
    ws.onMessage({ event: "forge_job_completed", ...msg });
  });

  it("refreshes the forge panel when a job finishes", async () => {
    const { result } = await mount();
    const before = result.current.forgeRefreshKey;

    complete({ status: "success", filename: "Movie.mkv" });

    expect(result.current.forgeRefreshKey).toBe(before + 1);
  });

  it("refreshes even when the error field is not a string", async () => {
    // .slice() on a non-string throws. With the toast first that threw before
    // the refresh, so the panel kept showing a job that had already finished.
    const { result } = await mount();
    const before = result.current.forgeRefreshKey;

    complete({ status: "failed", filename: "Movie.mkv", error: { code: 500 } });

    expect(result.current.forgeRefreshKey).toBe(before + 1);
  });

  it("still reports an error that is not a string", async () => {
    const { result } = await mount();

    complete({ status: "failed", filename: "Movie.mkv", error: { code: 500 } });

    expect(result.current.toasts).toHaveLength(1);
    expect(result.current.toasts[0].tone).toBe("error");
  });

  it("survives a message with no status at all", async () => {
    const { result } = await mount();
    const before = result.current.forgeRefreshKey;

    complete({ filename: "Movie.mkv" });

    expect(result.current.forgeRefreshKey).toBe(before + 1);
  });

  it("tones a successful job differently from an undone one", async () => {
    const { result } = await mount();

    complete({ status: "success", filename: "A.mkv" });
    complete({ status: "undone", filename: "B.mkv" });

    expect(result.current.toasts.map(t => t.tone)).toEqual(["success", "info"]);
  });
});


/* ── revert_complete ─────────────────────────────────────────────────────── */

describe("useAppData — revert_complete", () => {
  /**
   * A revert's POST returns as soon as the work has STARTED, so the panel
   * that requested it reloads against a file still being rewritten and
   * shows the entry as though nothing happened. This event is the only
   * signal that it finished.
   *
   * Reported: clicking revert on several entries in quick succession left
   * every one of them on screen. Only the first actually ran — the rest
   * were refused, one at a time being the server's rule — and nothing ever
   * refreshed, so all of them looked broken rather than one running and
   * the others declined.
   */
  it("bumps the revert refresh key so the panel reloads", () => {
    const { result } = renderHook(() => useAppData());
    const before = result.current.revertRefreshKey;

    act(() => {
      ws.onMessage({ event: "revert_complete", success: true,
        restored_path: "/media/tv/Show/S01E01.mkv" });
    });

    expect(result.current.revertRefreshKey).not.toBe(before);
  });

  it("reports the outcome, since the panel may not be on screen", () => {
    const { result } = renderHook(() => useAppData());

    act(() => {
      ws.onMessage({ event: "revert_complete", success: true,
        restored_path: "/media/tv/Show/S01E01.mkv" });
    });

    expect(result.current.toasts.at(-1).msg).toContain("S01E01.mkv");
    expect(result.current.toasts.at(-1).tone).toBe("success");
  });

  it("carries the reason when a revert fails", () => {
    // "This file has changed size since it was processed" is actionable;
    // a bare failure on a file sitting right there is not.
    const { result } = renderHook(() => useAppData());

    act(() => {
      ws.onMessage({ event: "revert_complete", success: false,
        error: "Show.mkv has changed size since it was processed" });
    });

    const last = result.current.toasts.at(-1);
    expect(last.tone).toBe("error");
    expect(last.msg).toContain("changed size");
  });

  it("refreshes the panel when a job completes, not only when one reverts", () => {
    /**
     * Reported: the recycle bin updated when entries were removed but not
     * when they were added. A completed job is the only thing that creates
     * a revert point, and fetchAll does not cover the recycle bin — it has
     * its own endpoint and its own key. So the panel only ever shrank, and
     * new entries appeared out of nowhere on the next manual reload.
     */
    const { result } = renderHook(() => useAppData());
    const before = result.current.revertRefreshKey;

    act(() => {
      ws.onMessage({ event: "job_completed", status: "success",
        filename: "S01E01.mkv" });
    });

    expect(result.current.revertRefreshKey).not.toBe(before);
  });

  it("does not throw on a message with nothing but a type", () => {
    // useWebSocket invokes this callback inside a bare catch, so anything
    // that throws here is swallowed and takes the refresh with it — the
    // exact failure job_completed was already fixed for.
    const { result } = renderHook(() => useAppData());
    const before = result.current.revertRefreshKey;

    act(() => { ws.onMessage({ event: "revert_complete" }); });

    expect(result.current.revertRefreshKey).not.toBe(before);
  });
});
