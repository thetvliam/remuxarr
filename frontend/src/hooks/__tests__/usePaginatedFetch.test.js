/**
 * usePaginatedFetch — the shared paginated-list hook.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * This hook backs three list surfaces (the two Language Review sections and
 * the Forge Candidates panel) and had no tests at all. Mutation testing
 * confirmed it: 15 mutations, 15 survivors. Every behaviour below could be
 * removed or inverted with the suite still green.
 *
 * Three areas carry real, documented, bug-fix-driven complexity and are
 * pinned deliberately rather than incidentally:
 *
 *   paramsKey       — extraParams is a fresh object literal on every render,
 *                     so it is serialised rather than depended on directly.
 *                     Blank values are dropped so a cleared filter produces
 *                     the same request as no filter; keys are sorted so
 *                     reordering the object does not trigger a reset.
 *
 *   generation      — the hook's own header calls this out: an old finally
 *                     block resetting loadingRef for a newer fetch causes
 *                     stale results or missing updates.
 *
 *   pageSize dep    — the source comment records that this was suppressed
 *                     from the dependency array, which silently switched the
 *                     rule off for the whole effect.
 *
 * Callers pass pageSize 100 (LanguageReviewSection) and a { language } filter,
 * so the tests below use that shape rather than the defaults.
 *
 * REFRESH IN PLACE
 * ----------------
 * A refreshKey change reloads the same list without clearing it, asking for
 * as many rows as were loaded; a change of endpoint, base URL, search or
 * extraParams is still a new list. "clears the previous list before the new
 * one arrives" used a refreshKey change and pinned the old truncation, so it
 * now uses a search.
 *
 * Mutation, 9 applied, 9 killed, against the suite with that one test moved.
 * Three were killed by it already: every change treated as a refresh, and
 * search or extraParams left out of what makes a new list. Six survived it
 * and are killed by the tests under "refresh in place": a refresh treated as
 * a new list, reloading one page, blanking the rows, resetting the offset
 * before the reply, and the endpoint or base URL left out of what makes a
 * new list.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePaginatedFetch } from "../usePaginatedFetch";

/* ── Harness ────────────────────────────────────────────────────────────── */

let calls;

/** Stub fetch with a server holding `total` items, serving pages of them. */
function mockServer({ total = 3, ok = true, extra = {} } = {}) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    const u = new URL(String(url), "http://localhost");
    const limit  = Number(u.searchParams.get("limit"));
    const offset = Number(u.searchParams.get("offset"));
    calls.push({
      url:      String(url),
      limit,
      offset,
      search:   u.searchParams.get("search"),
      language: u.searchParams.get("language"),
      keys:     [...u.searchParams.keys()],
    });
    const items = Array.from(
      { length: Math.max(0, Math.min(limit, total - offset)) },
      (_, i) => ({ id: offset + i }),
    );
    return { ok, json: async () => ({ items, total, ...extra }) };
  }));
}

/* Servers whose page does not account for the whole of `total`.
 *
 * Deliberately separate from mockServer rather than an option on it.
 * mockServer derives its page FROM total, which is what makes it a fair stand
 * in for a healthy endpoint — every page it serves advances the offset, so
 * every existing pagination test above asserts against a server that always
 * makes progress. Teaching it to under-serve would let those same tests run
 * against a server that does not, and the hasMore arithmetic they exist to
 * pin would then be satisfied by a mock that never exercises the short case. */
function mockUnderservingServer({ total = 250 } = {}) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    const u = new URL(String(url), "http://localhost");
    calls.push({ offset: Number(u.searchParams.get("offset")) });
    return { ok: true, json: async () => ({ items: [], total }) };
  }));
}

/** Serves a fixed number of rows per page, fewer than asked for but not none. */
function mockShortPageServer({ total = 250, served = 99 } = {}) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    const u = new URL(String(url), "http://localhost");
    const offset = Number(u.searchParams.get("offset"));
    calls.push({ offset });
    const items = Array.from({ length: served }, (_, i) => ({ id: offset + i }));
    return { ok: true, json: async () => ({ items, total }) };
  }));
}

/* refreshKey and extraParams are compared by identity / by serialisation
   respectively. refreshKey must be a stable reference across renders or the
   effect refires forever; extraParams deliberately need NOT be, which is the
   whole point of paramsKey and is asserted below. */
const K1 = { key: 1 };
const K2 = { key: 2 };
const EP = "/api/forge/candidates/";

/* ── Request construction ───────────────────────────────────────────────── */

describe("usePaginatedFetch — request construction", () => {
  beforeEach(() => mockServer());

  it("requests the caller's page size, not the default", async () => {
    renderHook(() => usePaginatedFetch("", EP, K1, "", 100));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].limit).toBe(100);
  });

  it("hits the caller's endpoint", async () => {
    renderHook(() => usePaginatedFetch("", EP, K1, "", 100));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].url).toContain(EP);
  });

  it("sends the trimmed search term", async () => {
    renderHook(() => usePaginatedFetch("", EP, K1, "  blade  ", 100));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].search).toBe("blade");
  });

  it("omits search when the term is blank", async () => {
    renderHook(() => usePaginatedFetch("", EP, K1, "   ", 100));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].keys).not.toContain("search");
  });

  it("sends extraParams as query params", async () => {
    renderHook(() => usePaginatedFetch("", EP, K1, "", 100, { language: "dut" }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].language).toBe("dut");
  });

  it("omits an extraParam that was cleared, rather than sending it blank", async () => {
    // A cleared filter must produce the same request as no filter at all —
    // sending language= blank makes the server filter on the empty string.
    renderHook(() => usePaginatedFetch("", EP, K1, "", 100, { language: "" }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].keys).not.toContain("language");
  });

  it("omits null and undefined extraParams", async () => {
    renderHook(() =>
      usePaginatedFetch("", EP, K1, "", 100, { language: null, kind: undefined }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(calls[0].keys).not.toContain("language");
    expect(calls[0].keys).not.toContain("kind");
  });
});

/* ── paramsKey stability ────────────────────────────────────────────────── */

describe("usePaginatedFetch — extraParams identity", () => {
  beforeEach(() => mockServer());

  it("a fresh object literal with the same contents does not refetch", async () => {
    // Callers build this inline on every render. If it were depended on
    // directly the list would refetch continuously.
    const { rerender } = renderHook(
      ({ lang }) => usePaginatedFetch("", EP, K1, "", 100, { language: lang }),
      { initialProps: { lang: "dut" } },
    );
    await waitFor(() => expect(calls.length).toBe(1));

    rerender({ lang: "dut" });                 // new object, same contents
    await new Promise(r => setTimeout(r, 20));
    expect(calls.length).toBe(1);
  });

  it("reordering the keys does not refetch", async () => {
    const { rerender } = renderHook(
      ({ p }) => usePaginatedFetch("", EP, K1, "", 100, p),
      { initialProps: { p: { language: "dut", kind: "audio" } } },
    );
    await waitFor(() => expect(calls.length).toBe(1));

    rerender({ p: { kind: "audio", language: "dut" } });
    await new Promise(r => setTimeout(r, 20));
    expect(calls.length).toBe(1);
  });

  it("changing an extraParam value does refetch", async () => {
    const { rerender } = renderHook(
      ({ lang }) => usePaginatedFetch("", EP, K1, "", 100, { language: lang }),
      { initialProps: { lang: "dut" } },
    );
    await waitFor(() => expect(calls.length).toBe(1));

    rerender({ lang: "ger" });
    await waitFor(() => expect(calls.length).toBe(2));
    expect(calls[1].language).toBe("ger");
  });

  it("a changed refreshKey refetches", async () => {
    const { rerender } = renderHook(
      ({ k }) => usePaginatedFetch("", EP, k, "", 100),
      { initialProps: { k: K1 } },
    );
    await waitFor(() => expect(calls.length).toBe(1));

    rerender({ k: K2 });
    await waitFor(() => expect(calls.length).toBe(2));
  });

  it("a changed pageSize refetches at the new size", async () => {
    // pageSize is closed over by doFetch. The source comment records that it
    // was suppressed from the dependency array, so a caller changing it kept
    // requesting the old size indefinitely.
    const { rerender } = renderHook(
      ({ n }) => usePaginatedFetch("", EP, K1, "", n),
      { initialProps: { n: 100 } },
    );
    await waitFor(() => expect(calls.length).toBe(1));

    rerender({ n: 25 });
    await waitFor(() => expect(calls.length).toBe(2));
    expect(calls[1].limit).toBe(25);
  });
});

/* ── Pagination ─────────────────────────────────────────────────────────── */

describe("usePaginatedFetch — pagination", () => {
  it("reports hasMore while the server holds more than one page", async () => {
    mockServer({ total: 250 });
    const { result } = renderHook(() => usePaginatedFetch("", EP, K1, "", 100));
    await waitFor(() => expect(result.current.items.length).toBe(100));

    expect(result.current.total).toBe(250);
    expect(result.current.hasMore).toBe(true);
  });

  it("clears hasMore once everything is loaded", async () => {
    mockServer({ total: 3 });
    const { result } = renderHook(() => usePaginatedFetch("", EP, K1, "", 100));
    await waitFor(() => expect(result.current.items.length).toBe(3));

    expect(result.current.hasMore).toBe(false);
  });

  it("loadMore appends the NEXT page rather than repeating the first", async () => {
    mockServer({ total: 250 });
    const { result } = renderHook(() => usePaginatedFetch("", EP, K1, "", 100));
    await waitFor(() => expect(result.current.items.length).toBe(100));

    await act(async () => { result.current.loadMore(); });
    await waitFor(() => expect(result.current.items.length).toBe(200));

    expect(calls.map(c => c.offset)).toEqual([0, 100]);
    const ids = result.current.items.map(i => i.id);
    expect(new Set(ids).size).toBe(ids.length);   // no duplicates
  });

  /* ── Pages that do not advance the offset ───────────────────────────────
   *
   * `total` and `items` come from two separate queries on this endpoint, and
   * the server drops rows from the page after counting them: get_candidates
   * skips a file whose AAC track vanished between the two queries, and
   * _language_review skips a flag whose MediaFile did. So a page can be short
   * of what `total` implies, and in the limit empty while `total` still counts
   * the rows.
   *
   * An empty page cannot advance offsetRef, so if hasMore stays true the
   * sentinel keeps rendering, the observer re-arms on every loading
   * transition (its deps are [hasMore, loading, loadMore]) and re-issues the
   * identical request without end. hasMore is what gates that, so it is what
   * these pin. */
  it("clears hasMore when a page arrives empty despite an unreached total", async () => {
    mockUnderservingServer({ total: 250 });
    const { result } = renderHook(() => usePaginatedFetch("", EP, K1, "", 100));
    await waitFor(() => expect(result.current.total).toBe(250));

    // No row was served, so there is no offset to advance to. Reporting
    // another page here is what makes the loop self-sustaining.
    expect(result.current.hasMore).toBe(false);
  });

  it("still reports more pages when a short page DID serve rows", async () => {
    /* The companion to the test above, and the reason the guard is on "no
     * rows" rather than "fewer rows than asked for". A page short by the odd
     * dropped row still advances the offset, so it terminates on its own and
     * must keep paginating — gating on newItems.length < pageSize would strand
     * the rest of the list behind a single skipped row. */
    mockShortPageServer({ total: 250, served: 99 });
    const { result } = renderHook(() => usePaginatedFetch("", EP, K1, "", 100));
    await waitFor(() => expect(result.current.items.length).toBe(99));

    expect(result.current.hasMore).toBe(true);
  });

  it("carries search and extraParams onto the next page too", async () => {
    mockServer({ total: 250 });
    const { result } = renderHook(() =>
      usePaginatedFetch("", EP, K1, "blade", 100, { language: "dut" }));
    await waitFor(() => expect(result.current.items.length).toBe(100));

    await act(async () => { result.current.loadMore(); });
    await waitFor(() => expect(calls.length).toBe(2));

    expect(calls[1].search).toBe("blade");
    expect(calls[1].language).toBe("dut");
  });
});

/* ── Reset between generations ──────────────────────────────────────────── */

describe("usePaginatedFetch — reset on dependency change", () => {
  it("clears the previous list before the new one arrives", async () => {
    /* A new search is a new list. A refreshKey change is not, and is
     * reloaded in place (see "refresh in place"), so this uses the search. */
    mockServer({ total: 250 });
    const { result, rerender } = renderHook(
      ({ s }) => usePaginatedFetch("", EP, K1, s, 100),
      { initialProps: { s: "" } },
    );
    await waitFor(() => expect(result.current.items.length).toBe(100));

    // Load a second page so offset is non-zero, then search.
    await act(async () => { result.current.loadMore(); });
    await waitFor(() => expect(result.current.items.length).toBe(200));

    rerender({ s: "show" });
    await waitFor(() => expect(calls.length).toBe(3));

    // The new list starts from the first page, not from offset 200 and not
    // from as many rows as the old one had loaded.
    expect(calls[2].offset).toBe(0);
    expect(calls[2].limit).toBe(100);
    await waitFor(() => expect(result.current.items.length).toBe(100));
  });

  it("empties the list IMMEDIATELY, not once the new page lands", async () => {
    /* The reset and the refetch are separated by a network round trip. If the
       old items are not cleared up front they stay on screen for the whole of
       it — the user sees the previous filter's rows under the new filter's
       heading. Asserting only the settled state cannot see this: both the
       reset and its absence end at the same list. */
    const pending = deferredServer();
    const { result, rerender } = renderHook(
      ({ lang }) => usePaginatedFetch("", EP, K1, "", 100, { language: lang }),
      { initialProps: { lang: "dut" } },
    );
    await waitFor(() => expect(pending.length).toBe(1));

    await act(async () => {
      pending[0].release([{ id: 1 }, { id: 2 }], 2);
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.items.length).toBe(2));
    expect(result.current.total).toBe(2);

    // Switch filter. The new request is in flight and has NOT resolved.
    rerender({ lang: "ger" });
    await waitFor(() => expect(pending.length).toBe(2));

    expect(result.current.items).toEqual([]);
    expect(result.current.total).toBe(0);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.raw).toBeNull();
  });
});

/* ── Raw response passthrough ───────────────────────────────────────────── */

describe("usePaginatedFetch — raw", () => {
  it("exposes the whole response body, not just items and total", async () => {
    // The audio review endpoint returns language facet counts alongside the
    // page; LanguageReviewSection reads them off `raw`.
    mockServer({ total: 2, extra: { language_counts: { dut: 7, ger: 3 } } });
    const { result } = renderHook(() => usePaginatedFetch("", EP, K1, "", 100));
    await waitFor(() => expect(result.current.items.length).toBe(2));

    expect(result.current.raw).toMatchObject({ language_counts: { dut: 7, ger: 3 } });
  });
});

/* ── Failure handling ───────────────────────────────────────────────────── */

describe("usePaginatedFetch — failure handling", () => {
  it("a non-ok response leaves the list empty rather than populating it", async () => {
    mockServer({ total: 5, ok: false });
    const { result } = renderHook(() => usePaginatedFetch("", EP, K1, "", 100));
    await waitFor(() => expect(calls.length).toBe(1));
    await new Promise(r => setTimeout(r, 20));

    expect(result.current.items).toEqual([]);
    expect(result.current.total).toBe(0);
    expect(result.current.raw).toBeNull();
    expect(result.current.loading).toBe(false);
  });
});

/* ── Superseded requests ────────────────────────────────────────────────── */
/* Controlled resolution ordering — the point is what happens when a request
   that has been REPLACED finishes AFTER the one replacing it. */

function deferredServer() {
  const pending = [];
  calls = [];
  vi.stubGlobal("fetch", vi.fn((url) => {
    const u = new URL(String(url), "http://localhost");
    calls.push({ language: u.searchParams.get("language") });
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

describe("usePaginatedFetch — superseded requests", () => {
  it("a stale response does not overwrite the current list", async () => {
    const pending = deferredServer();
    const { result, rerender } = renderHook(
      ({ lang }) => usePaginatedFetch("", EP, K1, "", 100, { language: lang }),
      { initialProps: { lang: "dut" } },
    );
    await waitFor(() => expect(pending.length).toBe(1));

    rerender({ lang: "ger" });
    await waitFor(() => expect(pending.length).toBe(2));

    await act(async () => {
      pending[1].release([{ id: 99 }], 1);     // current filter
      pending[0].release([{ id: 1 }], 500);    // superseded, lands last
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.items.length).toBe(1));
    expect(result.current.items.map(i => i.id)).toEqual([99]);
    expect(result.current.total).toBe(1);
  });

  it("a stale response does not clear the live request's loading flag", async () => {
    const pending = deferredServer();
    const { result, rerender } = renderHook(
      ({ lang }) => usePaginatedFetch("", EP, K1, "", 100, { language: lang }),
      { initialProps: { lang: "dut" } },
    );
    await waitFor(() => expect(pending.length).toBe(1));

    rerender({ lang: "ger" });
    await waitFor(() => expect(pending.length).toBe(2));

    await act(async () => {
      pending[0].release([{ id: 1 }], 500);    // only the stale one resolves
      await Promise.resolve();
    });

    expect(result.current.loading).toBe(true);
  });

  it("a generation change DURING json() parsing must not land stale items", async () => {
    const fetches = [];
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
      ({ lang }) => usePaginatedFetch("", EP, K1, "", 100, { language: lang }),
      { initialProps: { lang: "dut" } },
    );
    await waitFor(() => expect(fetches.length).toBe(1));

    // Resolve while still current, so it passes the post-fetch check.
    await act(async () => { fetches[0].releaseFetch(); await Promise.resolve(); });

    rerender({ lang: "ger" });                  // supersede it mid-parse
    await waitFor(() => expect(fetches.length).toBe(2));

    await act(async () => {
      fetches[0].releaseJson({ items: [{ id: 666 }], total: 999 });
      await Promise.resolve();
    });

    expect(result.current.items).toEqual([]);
    expect(result.current.total).toBe(0);
  });
});

/* ── Refresh in place ───────────────────────────────────────────────────── */

/* A refreshKey change is the same list again. Its callers bump it on every
 * finished job, queued file, finished scan and Forge add, and treating each
 * one as a new list blanked the rows mid-flight, sent the scroll position
 * back to the top and dropped every page loaded past the first. The first
 * three tests fail on the code that did; the fourth pins the other half,
 * that a change of endpoint or base URL is still a new list. */
describe("usePaginatedFetch — refresh in place", () => {
  it("a refresh keeps the rows on screen until the reply lands", async () => {
    const pending = deferredServer();
    const { result, rerender } = renderHook(
      ({ k }) => usePaginatedFetch("", EP, k, "", 100),
      { initialProps: { k: K1 } },
    );
    await waitFor(() => expect(pending.length).toBe(1));
    await act(async () => {
      pending[0].release([{ id: 1 }, { id: 2 }], 2);
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.items.length).toBe(2));

    rerender({ k: K2 });
    await waitFor(() => expect(pending.length).toBe(2));

    // In flight: the old rows and total are still what is shown.
    expect(result.current.items.map(i => i.id)).toEqual([1, 2]);
    expect(result.current.total).toBe(2);

    await act(async () => {
      pending[1].release([{ id: 2 }], 1);
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.items.map(i => i.id)).toEqual([2]));
    expect(result.current.total).toBe(1);
  });

  it("a refresh asks for as many rows as were loaded, and pages on after them", async () => {
    mockServer({ total: 250 });
    const { result, rerender } = renderHook(
      ({ k }) => usePaginatedFetch("", EP, k, "", 100),
      { initialProps: { k: K1 } },
    );
    await waitFor(() => expect(result.current.items.length).toBe(100));
    await act(async () => { result.current.loadMore(); });
    await waitFor(() => expect(result.current.items.length).toBe(200));

    rerender({ k: K2 });
    await waitFor(() => expect(calls.length).toBe(3));
    expect(calls[2]).toMatchObject({ offset: 0, limit: 200 });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items.length).toBe(200);
    expect(result.current.hasMore).toBe(true);

    await act(async () => { result.current.loadMore(); });
    await waitFor(() => expect(calls.length).toBe(4));
    expect(calls[3]).toMatchObject({ offset: 200, limit: 100 });
  });

  it("a refresh that fails keeps the rows and pages on from them", async () => {
    /* Paging from 0 again would append the first page under the rows
     * still shown. */
    mockServer({ total: 250 });
    const { result, rerender } = renderHook(
      ({ k }) => usePaginatedFetch("", EP, k, "", 100),
      { initialProps: { k: K1 } },
    );
    await waitFor(() => expect(result.current.items.length).toBe(100));
    await act(async () => { result.current.loadMore(); });
    await waitFor(() => expect(result.current.items.length).toBe(200));

    const working = globalThis.fetch;
    const failing = vi.fn(async () => ({ ok: false, json: async () => ({}) }));
    vi.stubGlobal("fetch", failing);
    rerender({ k: K2 });
    await waitFor(() => expect(failing).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items.length).toBe(200);

    vi.stubGlobal("fetch", working);
    await act(async () => { result.current.loadMore(); });
    await waitFor(() => expect(calls.length).toBe(3));
    expect(calls[2].offset).toBe(200);
  });

  it("a different endpoint or base URL is a new list, cleared up front", async () => {
    const pending = deferredServer();
    const { result, rerender } = renderHook(
      ({ base, ep }) => usePaginatedFetch(base, ep, K1, "", 100),
      { initialProps: { base: "", ep: EP } },
    );
    await waitFor(() => expect(pending.length).toBe(1));
    await act(async () => { pending[0].release([{ id: 1 }], 1); await Promise.resolve(); });
    await waitFor(() => expect(result.current.items.length).toBe(1));

    rerender({ base: "", ep: "/api/other/" });
    await waitFor(() => expect(pending.length).toBe(2));
    expect(result.current.items).toEqual([]);

    await act(async () => { pending[1].release([{ id: 2 }], 1); await Promise.resolve(); });
    await waitFor(() => expect(result.current.items.length).toBe(1));

    rerender({ base: "http://elsewhere", ep: "/api/other/" });
    await waitFor(() => expect(pending.length).toBe(3));
    expect(result.current.items).toEqual([]);
  });
});
