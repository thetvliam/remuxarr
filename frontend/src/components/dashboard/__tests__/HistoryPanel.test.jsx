/**
 * HistoryPanel — the summary counts behind the tab badges.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The badge counts came from the one fetch in this app with no protection on
 * it at all:
 *
 *   fetch(`${api}/api/history/summary`).then(r => r.json()).then(setCounts)
 *
 * No status check and no cleanup, in a component whose two neighbours —
 * useHistoryData and usePaginatedFetch — both carry generation guards and
 * abort controllers, with comments explaining at length why they are needed.
 *
 * Two things followed. Its effect re-runs on every historyRefreshKey bump,
 * and job completions bump it, so two requests could be in flight together
 * and the older one could land last, leaving badges that disagree with the
 * list under them until something else happened to refresh. And an HTTP
 * error parsed as data: every `d.success || 0` read undefined off the error
 * body, so a transient 500 rewrote all four badges to zero, over a list that
 * still had rows in it. The network-failure path was already correct — its
 * catch leaves the counts alone — so the two failure modes disagreed.
 *
 * These pin the counts on screen rather than the call count, per the
 * frontend testing note in tests/README.md.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HistoryPanel } from "../HistoryPanel";
import { ThemeProvider } from "../../../theme";

const COUNTS_A = { success: 12, failed: 3, skipped: 4, dry_run: 5 };
const COUNTS_B = { success: 70, failed: 8, skipped: 9, dry_run: 1 };

/** Resolvers for the summary requests, in the order they were made. */
let summary;

/**
 * The summary endpoint is left pending so a test can choose the order the
 * answers arrive in; everything else answers immediately. The list is empty
 * and total is 0 so hasMore stays false and the scroll observer is never
 * constructed — jsdom has no IntersectionObserver.
 */
function mockApi() {
  summary = [];
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    if (String(url).includes("/api/history/summary")) {
      return new Promise((resolve) => { summary.push(resolve); });
    }
    return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) };
  }));
}

/** Answer the Nth summary request with counts, or with an HTTP error. */
const answer = (n, counts) =>
  summary[n]({ ok: true, status: 200, json: async () => counts });
const fail = (n, status = 500) =>
  summary[n]({ ok: false, status, json: async () => ({ detail: "boom" }) });

function renderPanel(refreshKey = { key: 1, status: null }) {
  return render(
    <ThemeProvider>
      <HistoryPanel api="" historyRefreshKey={refreshKey}
                    onSelect={() => {}} onRetryAll={() => {}} onClearDryRun={() => {}} />
    </ThemeProvider>,
  );
}

/** The badge text for a tab, e.g. "SUCCESS12". Absent count means zero. */
const badge = (label) =>
  screen.getByRole("button", { name: new RegExp(`^${label}`) }).textContent;

/* jsdom has no IntersectionObserver, and this one records what it observes.
 * Stubbed for the whole file, not just the scroll tests below: without it the
 * counts tests would depend on this component never constructing an observer,
 * and would fail with a ReferenceError the moment it did — for a reason that
 * has nothing to do with what they assert. */
let observers;

class FakeObserver {
  constructor(cb) { this.cb = cb; this.targets = []; observers.push(this); }
  observe(el) { this.targets.push(el); }
  unobserve(el) { this.targets = this.targets.filter(t => t !== el); }
  disconnect() { this.targets = []; }
}

/** Whether anything is currently watching for the end of the list. */
const watching = () => observers.some(o => o.targets.length > 0);

beforeEach(() => {
  observers = [];
  vi.stubGlobal("IntersectionObserver", FakeObserver);
});

describe("HistoryPanel — summary counts", () => {
  beforeEach(() => mockApi());

  it("shows the counts the server returned", async () => {
    renderPanel();
    await waitFor(() => expect(summary).toHaveLength(1));
    answer(0, COUNTS_A);

    await waitFor(() => expect(badge("SUCCESS")).toContain("12"));
    expect(badge("FAILED")).toContain("3");
  });

  it("does not let a slow earlier response overwrite a newer one", async () => {
    /* Two refreshes in flight together, the first answering last. Without a
     * cleanup guard the stale answer is simply the last writer, and the
     * badges end up disagreeing with the list beneath them. */
    const { rerender } = renderPanel({ key: 1, status: null });
    await waitFor(() => expect(summary).toHaveLength(1));

    rerender(
      <ThemeProvider>
        <HistoryPanel api="" historyRefreshKey={{ key: 2, status: null }}
                      onSelect={() => {}} onRetryAll={() => {}} onClearDryRun={() => {}} />
      </ThemeProvider>,
    );
    await waitFor(() => expect(summary).toHaveLength(2));

    answer(1, COUNTS_B);                       // the newer refresh lands first
    await waitFor(() => expect(badge("SUCCESS")).toContain("70"));

    answer(0, COUNTS_A);                       // the older one lands after it

    await waitFor(() => expect(badge("SUCCESS")).toContain("70"));
    expect(badge("FAILED")).toContain("8");
  });

  it("keeps the counts it has when a refresh returns an HTTP error", async () => {
    /* An error body has no success/failed/skipped/dry_run, so every `|| 0`
     * read undefined and all four badges went to zero — over a list that
     * still had rows in it. */
    const { rerender } = renderPanel({ key: 1, status: null });
    await waitFor(() => expect(summary).toHaveLength(1));
    answer(0, COUNTS_A);
    await waitFor(() => expect(badge("SUCCESS")).toContain("12"));

    rerender(
      <ThemeProvider>
        <HistoryPanel api="" historyRefreshKey={{ key: 2, status: null }}
                      onSelect={() => {}} onRetryAll={() => {}} onClearDryRun={() => {}} />
      </ThemeProvider>,
    );
    await waitFor(() => expect(summary).toHaveLength(2));
    fail(1);

    await waitFor(() => expect(badge("SUCCESS")).toContain("12"));
    expect(badge("FAILED")).toContain("3");
  });

  it("still updates the counts on a successful refresh", async () => {
    // The guards must not freeze the badges at whatever loaded first.
    const { rerender } = renderPanel({ key: 1, status: null });
    await waitFor(() => expect(summary).toHaveLength(1));
    answer(0, COUNTS_A);
    await waitFor(() => expect(badge("SUCCESS")).toContain("12"));

    rerender(
      <ThemeProvider>
        <HistoryPanel api="" historyRefreshKey={{ key: 2, status: null }}
                      onSelect={() => {}} onRetryAll={() => {}} onClearDryRun={() => {}} />
      </ThemeProvider>,
    );
    await waitFor(() => expect(summary).toHaveLength(2));
    answer(1, COUNTS_B);

    await waitFor(() => expect(badge("SUCCESS")).toContain("70"));
  });
});

/**
 * The infinite-scroll gate.
 *
 * Two guards decide whether scrolling can ask for another page: the sentinel
 * is rendered only when hasMore, and the observer effect returns early unless
 * hasMore. They are redundant — the effect's own `!sentinel` check already
 * covers the rendered guard, since React nulls the ref before effects run —
 * so removing either one alone changes nothing observable. Only removing both
 * reopens the loop these were written to prevent, where a page that serves no
 * rows leaves the offset where it was and the observer re-arms on every
 * loading transition against a request that can never advance.
 *
 * So this pins the pair rather than either clause, and says so, because a
 * later reader looking at one of them alone will find no test naming it and
 * has to know that is deliberate.
 */
describe("HistoryPanel — infinite scroll gate", () => {
  const row = (id) => ({ id, status: "success", file: { filename: `f${id}.mkv` } });

  /** A list server: `total` rows overall, served `page` at a time. */
  function mockList({ total, page }) {
    summary = [];
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/api/history/summary")) {
        return new Promise((resolve) => { summary.push(resolve); });
      }
      const offset = Number(new URL(u, "http://x").searchParams.get("offset") || 0);
      const items = Array.from(
        { length: Math.max(0, Math.min(page, total - offset)) },
        (_, i) => row(offset + i + 1),
      );
      return { ok: true, status: 200, json: async () => ({ items, total }) };
    }));
  }

  it("watches for the end of the list while pages remain", async () => {
    mockList({ total: 4, page: 2 });
    renderPanel();

    await waitFor(() => expect(screen.getByText("f2.mkv")).toBeInTheDocument());
    expect(watching()).toBe(true);
  });

  it("loads the next page when the end of the list comes into view", async () => {
    // The positive control: without this, the test below would pass against a
    // component whose scrolling never loaded anything at all.
    mockList({ total: 4, page: 2 });
    renderPanel();
    await waitFor(() => expect(screen.getByText("f2.mkv")).toBeInTheDocument());

    await act(async () => {
      observers.at(-1).cb([{ isIntersecting: true }]);
    });

    await waitFor(() => expect(screen.getByText("f4.mkv")).toBeInTheDocument());
  });

  it("stops watching once the last page has been served", async () => {
    /* The kill. With neither guard, the observer stays armed against an
     * offset that cannot advance, and re-arms on every loading transition —
     * one request per cycle, without end. */
    mockList({ total: 2, page: 2 });
    renderPanel();

    await waitFor(() => expect(screen.getByText("f2.mkv")).toBeInTheDocument());
    expect(watching()).toBe(false);
  });

  it("stops watching when a page serves no rows despite an unreached total", async () => {
    /* The exact shape the hooks guard against: the server counts rows it then
     * drops from the page. Nothing can advance the offset, so nothing should
     * be watching for more. */
    summary = [];
    vi.stubGlobal("fetch", vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/api/history/summary")) {
        return new Promise((resolve) => { summary.push(resolve); });
      }
      const offset = Number(new URL(u, "http://x").searchParams.get("offset") || 0);
      // First page serves rows; every later page is empty while total says 99.
      const items = offset === 0 ? [row(1), row(2)] : [];
      return { ok: true, status: 200, json: async () => ({ items, total: 99 }) };
    }));
    renderPanel();
    await waitFor(() => expect(screen.getByText("f2.mkv")).toBeInTheDocument());

    await act(async () => {
      observers.at(-1).cb([{ isIntersecting: true }]);
    });

    await waitFor(() => expect(watching()).toBe(false));
  });
});
