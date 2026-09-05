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
import { render, screen, waitFor } from "@testing-library/react";
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
