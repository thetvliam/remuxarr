/**
 * useHistoryData — the hook body.
 *
 * The sibling file (useHistoryData.test.js) covers eventAffectsTab, the pure
 * mapping. It is good and stays. But it was the ONLY thing covering this
 * module: the hook itself — request construction, relevance gating,
 * pagination, and the generation guards that make superseded requests safe —
 * ran in no test at all. An independent audit found 9 of 11 mutations of this
 * file surviving the whole frontend suite.
 *
 * That module also sat at 100% branch coverage on 41% of lines, which is the
 * clearest illustration in this codebase of why coverage is not a quality
 * signal: every branch of the one tested function was green while the hook
 * body was never executed.
 *
 * TWO HARNESS CONSTRAINTS THIS FILE DEPENDS ON
 *
 * 1. refreshKey must be referentially stable. It sits in the effect's
 *    dependency array and is compared by identity, so building it inline
 *    inside renderHook(() => useHistoryData("", "all", { key: 1 }, ""))
 *    creates a fresh object every render: effect refires, setState, rerender,
 *    forever. That does not fail cleanly — it OOMs the vitest worker after a
 *    few minutes. Hence the module-level constants below. This mirrors
 *    production, where historyRefreshKey is a useState object whose identity
 *    changes only on invalidation.
 *
 * 2. The generation guards need hand-released promises. A plain async mock
 *    resolves too promptly to interleave, and the entire point of those
 *    guards is what happens when a SUPERSEDED request finishes after the one
 *    that replaced it. deferredServer() below hands back release handles so a
 *    test can land responses in a chosen order.
 */
import { renderHook, waitFor, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useHistoryData } from "../useHistoryData";

// Referentially stable keys — see constraint 1 above.
const K_NULL      = { key: 0, status: null };
const K1_NULL     = { key: 1, status: null };
const K2_SUCCESS  = { key: 2, status: "success" };
const K2_FAILED   = { key: 2, status: "failed" };
const STALE_FAILED = { key: 7, status: "failed" };

let calls;

function mockServer({ items = [], total = 0, ok = true } = {}) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn((url) => {
    const u = new URL(String(url), "http://localhost");
    calls.push({
      url:    String(url),
      status: u.searchParams.get("status"),
      search: u.searchParams.get("search"),
      offset: u.searchParams.get("offset"),
      limit:  u.searchParams.get("limit"),
    });
    return Promise.resolve({ ok, json: async () => ({ items, total }) });
  }));
}

/** A fetch mock whose responses are released by the test, in its chosen order. */
function deferredServer() {
  const pending = [];
  calls = [];
  vi.stubGlobal("fetch", vi.fn((url) => {
    const u = new URL(String(url), "http://localhost");
    calls.push({ status: u.searchParams.get("status"),
                 offset: u.searchParams.get("offset") });
    let release;
    const p = new Promise((resolve) => { release = resolve; });
    pending.push({
      release: (items, total) =>
        release({ ok: true, json: async () => ({ items, total }) }),
    });
    return p;
  }));
  return pending;
}

/**
 * deferredServer's sibling, honouring AbortSignal the way a real fetch does:
 * an aborted request rejects with an AbortError instead of resolving. It also
 * hands back the signals, so a test can assert what was aborted rather than
 * only what was received.
 *
 * Deliberately a second function, with deferredServer left ignoring the
 * signal. The generation-guard tests below release a SUPERSEDED request and
 * assert its page is discarded — if aborting rejected that promise, the
 * release would land on an already-settled one and those tests would pass
 * with the generation counter deleted. The two groups want opposite things
 * from an abort, so they get a server each.
 */
function abortAwareServer() {
  const pending = [];
  const signals = [];
  calls = [];
  vi.stubGlobal("fetch", vi.fn((url, opts) => {
    const u = new URL(String(url), "http://localhost");
    calls.push({ status: u.searchParams.get("status"),
                 offset: u.searchParams.get("offset") });
    signals.push(opts?.signal);
    let release;
    const p = new Promise((resolve, reject) => {
      release = resolve;
      opts?.signal?.addEventListener("abort", () => {
        const err = new Error("aborted");
        err.name = "AbortError";
        reject(err);
      });
    });
    pending.push({
      release: (items, total) =>
        release({ ok: true, json: async () => ({ items, total }) }),
    });
    return p;
  }));
  return { pending, signals };
}

const item = (id) => ({ id, status: "success", filename: `f${id}.mkv` });

beforeEach(() => { calls = []; });
afterEach(() => { vi.unstubAllGlobals(); });


// ── The mount-gating regression ──────────────────────────────────────────────

describe("mount", () => {
  it("always fetches on mount, even when the standing refreshKey is irrelevant", async () => {
    /**
     * THE production bug, found by an independent audit and reproduced before
     * fixing: the first effect run has no previous state to compare against,
     * so it was misread as a refreshKey-only change and gated like one.
     *
     * Reachable path: HistoryPanel is mounted only while page === "dashboard",
     * so it unmounts on navigation; historyRefreshKey lives in useAppData and
     * survives. Navigate away, have a job fail, come back — the panel
     * remounts on its default "success" tab, sees a standing failed key,
     * decides it is irrelevant, and never fetches. The tab renders
     * permanently empty.
     *
     * Before the fix this asserted 0 fetches and 0 items.
     */
    mockServer({ items: [item(1)], total: 1 });

    const { result } = renderHook(() =>
      useHistoryData("", "success", STALE_FAILED, ""));

    // Wait on the STATE, not on calls.length. The counter increments the
    // moment fetch is INVOKED, whereas the response promise, json() and the
    // re-render all land in later microtasks — so a waitFor on the counter
    // can be satisfied before any state exists. That passed consistently
    // locally and failed in CI, which is slower and lost the race. The items
    // array is the real subject of this test anyway: it is what the user sees
    // when the bug is present, and reaching it implies the fetch happened.
    await waitFor(() => expect(result.current.items).toHaveLength(1));
    expect(calls).toHaveLength(1);
  });

  it("still fetches on mount when the refreshKey is relevant", async () => {
    mockServer({ items: [item(1)], total: 1 });

    renderHook(() => useHistoryData("", "success", K2_SUCCESS, ""));

    await waitFor(() => expect(calls.length).toBe(1));
  });
});


// ── Request construction ─────────────────────────────────────────────────────

describe("request construction", () => {
  it("sends the tab as a status filter", async () => {
    mockServer();

    renderHook(() => useHistoryData("", "failed", K_NULL, ""));

    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].status).toBe("failed");
  });

  it("omits the status filter on the all tab", async () => {
    /** "all" is the absence of a filter, not a literal status value — sending
     *  status=all would match nothing server-side. */
    mockServer();

    renderHook(() => useHistoryData("", "all", K_NULL, ""));

    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].status).toBeNull();
  });

  it("sends a trimmed search term", async () => {
    mockServer();

    renderHook(() => useHistoryData("", "all", K_NULL, "  blade runner  "));

    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].search).toBe("blade runner");
  });

  it("omits an empty or whitespace-only search", async () => {
    mockServer();

    renderHook(() => useHistoryData("", "all", K_NULL, "   "));

    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].search).toBeNull();
  });

  it("starts at offset zero with the page size as the limit", async () => {
    mockServer();

    renderHook(() => useHistoryData("", "all", K_NULL, ""));

    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].offset).toBe("0");
    expect(calls[0].limit).toBe("50");
  });
});


// ── Relevance gating ─────────────────────────────────────────────────────────

describe("relevance gating", () => {
  it("an irrelevant refreshKey does not refetch or disturb what is shown", async () => {
    /** The whole reason the gate exists: a failed job used to blank and
     *  reload the Success tab, producing a visible flash on a tab nothing
     *  changed on. */
    mockServer({ items: [item(1)], total: 1 });

    const { result, rerender } = renderHook(
      ({ key }) => useHistoryData("", "success", key, ""),
      { initialProps: { key: K1_NULL } },
    );
    await waitFor(() => expect(result.current.items).toHaveLength(1));

    rerender({ key: K2_FAILED });

    await new Promise((r) => setTimeout(r, 50));
    expect(calls.length).toBe(1);
    expect(result.current.items).toHaveLength(1);
  });

  it("an irrelevant refreshKey does not discard a first page still in flight", async () => {
    /**
     * The gate is about not disturbing what is ALREADY on screen. A first
     * page still loading is not that, and it used to be killed anyway: React
     * runs the previous effect's cleanup before every re-run, so the cleanup's
     * abort fired and the gated run then returned without issuing a
     * replacement. The aborted request's finally still matched its own
     * generation — the early return sits above the increment — so it cleared
     * loading on the way out, leaving an empty list with nothing in flight.
     *
     * Unrecoverable in the UI: hasMore is false so no scroll sentinel renders
     * to retrigger it, and HistoryPanel's badge counts are not gated, so the
     * tab read the real total above "No success items" until the user
     * switched tabs.
     *
     * Needs abortAwareServer specifically. Under a mock that ignores the
     * signal the response lands regardless and the bug is invisible, which is
     * why the existing coverage here did not see it.
     */
    const { pending } = abortAwareServer();

    const { result, rerender } = renderHook(
      ({ key }) => useHistoryData("", "success", key, ""),
      { initialProps: { key: K1_NULL } },
    );
    await waitFor(() => expect(pending.length).toBe(1));

    // A failed job completes while the Success tab is still loading.
    rerender({ key: K2_FAILED });

    await act(async () => { pending[0].release([item(1)], 1); });

    await waitFor(() => expect(result.current.items).toHaveLength(1));
    // Still gated: the page arrived because it was never cancelled, not
    // because the bump triggered a second request.
    expect(calls.length).toBe(1);
  });

  it("an irrelevant refreshKey does not discard an in-flight loadMore page", async () => {
    /** The same abort, on an append rather than a first page. Milder — the
     *  sentinel stays mounted at hasMore true, so scrolling can retrigger it
     *  — but the page in flight was still thrown away. */
    const { pending } = abortAwareServer();

    const { result, rerender } = renderHook(
      ({ key }) => useHistoryData("", "success", key, ""),
      { initialProps: { key: K1_NULL } },
    );
    await waitFor(() => expect(pending.length).toBe(1));
    await act(async () => { pending[0].release([item(1)], 2); });
    await waitFor(() => expect(result.current.hasMore).toBe(true));

    act(() => { result.current.loadMore(); });
    await waitFor(() => expect(pending.length).toBe(2));

    rerender({ key: K2_FAILED });
    await act(async () => { pending[1].release([item(2)], 2); });

    await waitFor(() => expect(result.current.items).toHaveLength(2));
    expect(result.current.items.map((i) => i.id)).toEqual([1, 2]);
  });

  it("a relevant refreshKey refetches", async () => {
    mockServer({ items: [item(1)], total: 1 });

    const { rerender } = renderHook(
      ({ key }) => useHistoryData("", "success", key, ""),
      { initialProps: { key: K1_NULL } },
    );
    await waitFor(() => expect(calls.length).toBe(1));

    rerender({ key: K2_SUCCESS });

    await waitFor(() => expect(calls.length).toBe(2));
  });

  it("a tab switch always refetches, whatever the refreshKey says", async () => {
    /** Gating applies to refreshKey bumps only. Switching tabs must always
     *  show fresh data, or the user sees another tab's rows. */
    mockServer({ items: [item(1)], total: 1 });

    const { rerender } = renderHook(
      ({ status }) => useHistoryData("", status, STALE_FAILED, ""),
      { initialProps: { status: "success" } },
    );
    await waitFor(() => expect(calls.length).toBe(1));

    rerender({ status: "skipped" });

    await waitFor(() => expect(calls.length).toBe(2));
    expect(calls[1].status).toBe("skipped");
  });

  it("a search change always refetches", async () => {
    mockServer({ items: [item(1)], total: 1 });

    const { rerender } = renderHook(
      ({ search }) => useHistoryData("", "success", STALE_FAILED, search),
      { initialProps: { search: "" } },
    );
    await waitFor(() => expect(calls.length).toBe(1));

    rerender({ search: "dune" });

    await waitFor(() => expect(calls.length).toBe(2));
    expect(calls[1].search).toBe("dune");
  });
});


// ── Pagination ───────────────────────────────────────────────────────────────

describe("pagination", () => {
  it("reports more pages when the loaded count is short of the total", async () => {
    mockServer({ items: [item(1)], total: 10 });

    const { result } = renderHook(() => useHistoryData("", "all", K_NULL, ""));

    await waitFor(() => expect(result.current.total).toBe(10));
    expect(result.current.hasMore).toBe(true);
  });

  it("reports no more pages once everything is loaded", async () => {
    mockServer({ items: [item(1)], total: 1 });

    const { result } = renderHook(() => useHistoryData("", "all", K_NULL, ""));

    await waitFor(() => expect(result.current.items).toHaveLength(1));
    expect(result.current.hasMore).toBe(false);
  });

  it("loadMore requests the next page and appends to what is shown", async () => {
    /** The offset must advance. Refetching from 0 and appending duplicates
     *  every row already on screen. */
    let batch = [item(1)];
    calls = [];
    vi.stubGlobal("fetch", vi.fn((url) => {
      const u = new URL(String(url), "http://localhost");
      calls.push({ offset: u.searchParams.get("offset") });
      return Promise.resolve({
        ok: true, json: async () => ({ items: batch, total: 2 }),
      });
    }));

    const { result } = renderHook(() => useHistoryData("", "all", K_NULL, ""));
    await waitFor(() => expect(result.current.items).toHaveLength(1));

    batch = [item(2)];
    await act(async () => { result.current.loadMore(); });

    await waitFor(() => expect(result.current.items).toHaveLength(2));
    expect(calls[1].offset).toBe("1");
    expect(result.current.items.map((i) => i.id)).toEqual([1, 2]);
  });
});


// ── Failure handling ─────────────────────────────────────────────────────────

describe("failure handling", () => {
  it("a failed loadMore does not destroy the pagination state", async () => {
    /**
     * Where the r.ok guard actually bites. On a fresh fetch the effect has
     * already reset items/total to empty, so honouring or ignoring the status
     * code look identical — which is why an earlier version of this test
     * proved nothing and let the mutation survive.
     *
     * On an APPEND it is visible. A FastAPI error carries a {"detail": ...}
     * body that json() parses happily, so without the guard `data.total ?? 0`
     * writes 0 over a real total of 10 and hasMore goes false — the user's
     * loaded rows stay on screen with the count reset and infinite scroll
     * dead, from one transient 500.
     */
    let failing = false;
    calls = [];
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(
      failing
        ? { ok: false, json: async () => ({ detail: "Internal Server Error" }) }
        : { ok: true,  json: async () => ({ items: [item(1)], total: 10 }) },
    )));

    const { result } = renderHook(() => useHistoryData("", "all", K_NULL, ""));
    await waitFor(() => expect(result.current.total).toBe(10));
    expect(result.current.hasMore).toBe(true);

    failing = true;
    await act(async () => { result.current.loadMore(); });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.total).toBe(10);
    expect(result.current.hasMore).toBe(true);
    expect(result.current.items).toHaveLength(1);
  });

  it("clears loading even when the request fails", async () => {
    /** A stuck loading flag blocks every future fetch — loadingRef gates
     *  doFetch, so one unfinished request wedges the panel for good. */
    calls = [];
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("network"))));

    const { result } = renderHook(() => useHistoryData("", "all", K_NULL, ""));

    await waitFor(() => expect(result.current.loading).toBe(false));
  });
});


// ── Superseded requests ──────────────────────────────────────────────────────

describe("generation guards", () => {
  it("a superseded response does not overwrite the current tab's rows", async () => {
    /** The race the generation counter exists for: switch tabs while a
     *  request is in flight, and the slow first response must not land under
     *  the new tab. */
    const pending = deferredServer();

    const { result, rerender } = renderHook(
      ({ status }) => useHistoryData("", status, K_NULL, ""),
      { initialProps: { status: "success" } },
    );
    await waitFor(() => expect(pending.length).toBe(1));

    rerender({ status: "failed" });
    await waitFor(() => expect(pending.length).toBe(2));

    // The NEW request lands first, then the superseded one.
    await act(async () => { pending[1].release([item(2)], 1); });
    await waitFor(() => expect(result.current.items).toHaveLength(1));
    await act(async () => { pending[0].release([item(999)], 500); });

    await new Promise((r) => setTimeout(r, 50));
    expect(result.current.items.map((i) => i.id)).toEqual([2]);
    expect(result.current.total).toBe(1);
  });

  it("a superseded request does not clear the current request's loading flag", async () => {
    /**
     * The subtle half. Without the generation check in the finally block, the
     * OLD request's cleanup resets loadingRef for the NEW one — which then
     * looks idle while still in flight, so the next doFetch is allowed
     * through and results interleave.
     */
    const pending = deferredServer();

    const { result, rerender } = renderHook(
      ({ status }) => useHistoryData("", status, K_NULL, ""),
      { initialProps: { status: "success" } },
    );
    await waitFor(() => expect(pending.length).toBe(1));

    rerender({ status: "failed" });
    await waitFor(() => expect(pending.length).toBe(2));

    // Superseded request finishes while the new one is still in flight.
    await act(async () => { pending[0].release([item(999)], 500); });

    expect(result.current.loading).toBe(true);
  });

  it("a generation change during body parsing still discards the page", async () => {
    /**
     * The narrowest window: the response has arrived and passed the
     * post-fetch generation check, but json() is still pending when the
     * effect re-runs. Verified genuinely reachable rather than
     * defensive-only — with the post-json check removed, the stale page
     * lands.
     */
    const fetches = [];
    calls = [];
    vi.stubGlobal("fetch", vi.fn(() => {
      let releaseFetch, releaseJson;
      const jsonP = new Promise((res) => { releaseJson = res; });
      const p = new Promise((res) => {
        releaseFetch = () => res({ ok: true, json: () => jsonP });
      });
      fetches.push({ releaseFetch, releaseJson });
      return p;
    }));

    const { result, rerender } = renderHook(
      ({ status }) => useHistoryData("", status, K_NULL, ""),
      { initialProps: { status: "success" } },
    );
    await waitFor(() => expect(fetches.length).toBe(1));

    // Response arrives, body still parsing.
    await act(async () => { fetches[0].releaseFetch(); });

    // Effect re-runs while json() is pending.
    rerender({ status: "failed" });
    await waitFor(() => expect(fetches.length).toBe(2));

    // Now the stale body resolves.
    await act(async () => {
      fetches[0].releaseJson({ items: [item(666)], total: 999 });
    });

    await new Promise((r) => setTimeout(r, 50));
    expect(result.current.items).toHaveLength(0);
    expect(result.current.total).toBe(0);
  });

  it("a tab switch resets the visible rows immediately", async () => {
    /** No stale rows under the new tab's header while its fetch is in
     *  flight — the reset is synchronous with the effect. */
    const pending = deferredServer();

    const { result, rerender } = renderHook(
      ({ status }) => useHistoryData("", status, K_NULL, ""),
      { initialProps: { status: "success" } },
    );
    await waitFor(() => expect(pending.length).toBe(1));
    await act(async () => { pending[0].release([item(1)], 1); });
    await waitFor(() => expect(result.current.items).toHaveLength(1));

    rerender({ status: "failed" });

    expect(result.current.items).toHaveLength(0);
  });
});


// ── Aborting ─────────────────────────────────────────────────────────────────

describe("aborting", () => {
  /* Both aborts the hook performs, pinned separately, because they are now
   * issued from different places and the reason for each differs. The gating
   * tests above cover the third case: the run that must NOT abort. */

  it("aborts a superseded request when it issues the replacement", async () => {
    /** The abort at the top of the effect body. Without it a tab switch
     *  leaves the old request running to completion against a server that no
     *  longer has a reader — the generation counter discards the result, but
     *  the request itself was never cancelled. */
    const { signals } = abortAwareServer();

    const { rerender } = renderHook(
      ({ status }) => useHistoryData("", status, K_NULL, ""),
      { initialProps: { status: "success" } },
    );
    await waitFor(() => expect(signals.length).toBe(1));

    rerender({ status: "failed" });

    await waitFor(() => expect(signals.length).toBe(2));
    expect(signals[0].aborted).toBe(true);
  });

  it("aborts the in-flight request on unmount", async () => {
    /** The unmount-only effect. HistoryPanel unmounts on every navigation
     *  away from the dashboard, so this is the common case, not a teardown
     *  nicety. */
    const { signals } = abortAwareServer();

    const { unmount } = renderHook(() =>
      useHistoryData("", "success", K1_NULL, ""));
    await waitFor(() => expect(signals.length).toBe(1));
    expect(signals[0].aborted).toBe(false);

    unmount();

    expect(signals[0].aborted).toBe(true);
  });
});
