/**
 * useActions — every mutating user action.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * useActions is the write half of the app: every button that changes server
 * state routes through here. It had no tests — 18 mutations, 18 survivors.
 *
 * Three clusters carry documented, already-shipped bugs whose incident notes
 * live in the source and nowhere else:
 *
 *   THE DRY RUN INTERLOCK
 *     toggleDryRun updates optimistically and MUST roll back on failure. With
 *     the rollback gone the header shows ◆ DRY RUN and a toast confirms it
 *     while the backend goes on actually remuxing files. The source calls this
 *     "the difference between a preview and an irreversible write".
 *
 *   THE INVALIDATION TAGS
 *     useAppData's docstring records four call sites that needed
 *     invalidateHistory and did not have it, leaving dismissed and cancelled
 *     rows on screen. The tag matters as much as the call: retryItem is tagged
 *     FROM THE ITEM because it also backs the modal's ▶ PROCESS NOW button for
 *     dry_run rows, and eventAffectsTab("failed", "dry_run") is false — a
 *     hardcoded "failed" left the Dry Run tab showing a row that no longer
 *     existed. dismissQueueItem and clearQueue tag "failed" because the DELETE
 *     sets status "cancelled" and history.py folds cancelled into that tab.
 *
 *   THE COUNTS
 *     retryAllFailed reads its counts straight into UI copy. "needs review"
 *     items are not done — they are waiting on the user — and folding them
 *     into either other count hid that entirely.
 *
 * The hook takes the whole useAppData bundle and has no state of its own, so
 * these tests pass spies for every setter and assert on what it calls.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";

import { useActions } from "../useActions";

/* ── Harness ────────────────────────────────────────────────────────────── */

let deps;
let responses;

/** Queue a response per fetch call, in order. */
function mockFetch(...queued) {
  responses = [...queued];
  vi.stubGlobal("fetch", vi.fn(async (url, opts = {}) => {
    const next = responses.shift() ?? { ok: true, body: {} };
    fetchCalls.push({ url: String(url), method: opts.method, body: opts.body });
    if (next.throws) throw new Error("network down");
    return { ok: next.ok !== false, json: async () => next.body ?? {} };
  }));
}

let fetchCalls;

beforeEach(() => {
  fetchCalls = [];
  deps = {
    api: "",
    dryRun: false,          setDryRun:       vi.fn(),
    workerPaused: false,    setWorkerPaused: vi.fn(),
    autoStart: true,        setAutoStart:    vi.fn(),
    setScanning:      vi.fn(),
    setModal:         vi.fn(),
    toast:            vi.fn(),
    fetchAll:         vi.fn(),
    fetchForge:       vi.fn(),
    invalidateHistory: vi.fn(),
    setForgeRefreshKey: vi.fn(),
  };
});

/** Build the hook. It holds no state, so calling it directly is safe.
 *  Named with a `use` prefix so react-hooks/rules-of-hooks recognises it as a
 *  custom hook rather than a plain function calling a hook illegally — the
 *  rule is satisfied rather than suppressed. */
const useSubject = (overrides = {}) => useActions({ ...deps, ...overrides });

const ITEM = { id: 7, status: "failed", file: { filename: "Movie.mkv" } };

/* ── The dry run interlock ──────────────────────────────────────────────── */

describe("useActions — toggleDryRun", () => {
  it("sends the TOGGLED value, not the current one", async () => {
    mockFetch({ ok: true });
    await useSubject({ dryRun: false }).toggleDryRun();

    expect(JSON.parse(fetchCalls[0].body)).toEqual({ value: true });
  });

  it("turns dry run off when it was on", async () => {
    mockFetch({ ok: true });
    await useSubject({ dryRun: true }).toggleDryRun();

    expect(JSON.parse(fetchCalls[0].body)).toEqual({ value: false });
    expect(deps.setDryRun).toHaveBeenCalledWith(false);
  });

  it("rolls the toggle back when the request fails", async () => {
    // Without this the header claims DRY RUN while the backend remuxes.
    mockFetch({ ok: false });
    await useSubject({ dryRun: false }).toggleDryRun();

    expect(deps.setDryRun).toHaveBeenNthCalledWith(1, true);   // optimistic
    expect(deps.setDryRun).toHaveBeenNthCalledWith(2, false);  // rolled back
    expect(deps.setDryRun).toHaveBeenLastCalledWith(false);
  });

  it("rolls back on a network error too", async () => {
    mockFetch({ throws: true });
    await useSubject({ dryRun: false }).toggleDryRun();

    expect(deps.setDryRun).toHaveBeenLastCalledWith(false);
  });

  it("reports the failure loudly rather than silently", async () => {
    mockFetch({ ok: false });
    await useSubject({ dryRun: false }).toggleDryRun();

    expect(deps.toast).toHaveBeenCalledTimes(1);
    const [msg, tone] = deps.toast.mock.calls[0];
    expect(tone).toBe("error");
    expect(msg).toMatch(/dry run/i);
  });
});

/* ── Worker controls ────────────────────────────────────────────────────── */

describe("useActions — togglePause", () => {
  it("pauses a running worker", async () => {
    mockFetch({ ok: true });
    await useSubject({ workerPaused: false }).togglePause();

    expect(fetchCalls[0].url).toContain("/api/worker/pause");
    expect(deps.setWorkerPaused).toHaveBeenCalledWith(true);
  });

  it("resumes a paused worker", async () => {
    mockFetch({ ok: true });
    await useSubject({ workerPaused: true }).togglePause();

    expect(fetchCalls[0].url).toContain("/api/worker/resume");
    expect(deps.setWorkerPaused).toHaveBeenCalledWith(false);
  });

  it("does not flip the UI when the request fails", async () => {
    mockFetch({ ok: false });
    await useSubject({ workerPaused: false }).togglePause();

    expect(deps.setWorkerPaused).not.toHaveBeenCalled();
  });
});

describe("useActions — abortJob", () => {
  it("disables auto-start so the queue stops rather than skipping ahead", async () => {
    // Protects a user who starts a scan without dry run and needs the WHOLE
    // queue to stop, not just the current file.
    mockFetch({ ok: true });
    await useSubject().abortJob(3);

    expect(deps.setAutoStart).toHaveBeenCalledWith(false);
  });

  it("leaves auto-start alone when the abort fails", async () => {
    mockFetch({ ok: false });
    await useSubject().abortJob(3);

    expect(deps.setAutoStart).not.toHaveBeenCalled();
  });
});

/* ── History invalidation ───────────────────────────────────────────────── */

describe("useActions — retryItem", () => {
  it("tags the invalidation from the item, not a hardcoded 'failed'", async () => {
    // The modal's ▶ PROCESS NOW button routes dry_run items through here, and
    // eventAffectsTab("failed", "dry_run") is false — a hardcoded tag left the
    // Dry Run tab showing a row that had already been deleted.
    mockFetch({ ok: true });
    await useSubject().retryItem({ ...ITEM, status: "dry_run" });

    expect(deps.invalidateHistory).toHaveBeenCalledWith("dry_run");
  });

  it("passes a failed item's own status through", async () => {
    mockFetch({ ok: true });
    await useSubject().retryItem({ ...ITEM, status: "failed" });

    expect(deps.invalidateHistory).toHaveBeenCalledWith("failed");
  });

  it("falls back to null when the item has no status", async () => {
    mockFetch({ ok: true });
    await useSubject().retryItem({ id: 7, file: { filename: "x.mkv" } });

    expect(deps.invalidateHistory).toHaveBeenCalledWith(null);
  });

  it("does not invalidate, close the modal, or claim success on failure", async () => {
    mockFetch({ ok: false });
    await useSubject().retryItem(ITEM);

    expect(deps.invalidateHistory).not.toHaveBeenCalled();
    expect(deps.setModal).not.toHaveBeenCalled();
    expect(deps.toast).toHaveBeenCalledWith(expect.stringMatching(/could not/i), "error");
  });
});

describe("useActions — dismissItem", () => {
  it("invalidates history so the dismissed row leaves the panel", async () => {
    // fetchAll refetches queue/active/review/worker/scan — never history.
    mockFetch({ ok: true });
    await useSubject().dismissItem(ITEM);

    expect(deps.invalidateHistory).toHaveBeenCalledWith("failed");
  });

  it("tags from the item, since it could have been in any tab", async () => {
    mockFetch({ ok: true });
    await useSubject().dismissItem({ ...ITEM, status: "success" });

    expect(deps.invalidateHistory).toHaveBeenCalledWith("success");
  });

  it("does not invalidate when the DELETE fails", async () => {
    mockFetch({ ok: false });
    await useSubject().dismissItem(ITEM);

    expect(deps.invalidateHistory).not.toHaveBeenCalled();
  });
});

/* ── Queue actions ──────────────────────────────────────────────────────── */

describe("useActions — dismissQueueItem", () => {
  it("treats a non-ok response as a failure, not just a thrown one", async () => {
    // fetch only rejects on a network failure, so a catch alone let a 500
    // through to the success toast.
    mockFetch({ ok: false });
    await useSubject().dismissQueueItem(ITEM);

    expect(deps.toast).toHaveBeenCalledWith("Failed to remove item", "error");
    expect(deps.toast).not.toHaveBeenCalledWith(
      expect.stringMatching(/removed from queue/i), expect.anything());
  });

  it("reports failure on a network error too", async () => {
    mockFetch({ throws: true });
    await useSubject().dismissQueueItem(ITEM);

    expect(deps.toast).toHaveBeenCalledWith("Failed to remove item", "error");
  });

  it("tags the invalidation 'failed', because the item becomes 'cancelled'", async () => {
    // history.py folds cancelled into the Failed tab.
    mockFetch({ ok: true });
    await useSubject().dismissQueueItem(ITEM);

    expect(deps.invalidateHistory).toHaveBeenCalledWith("failed");
  });

  it("does not invalidate when the request failed", async () => {
    mockFetch({ ok: false });
    await useSubject().dismissQueueItem(ITEM);

    expect(deps.invalidateHistory).not.toHaveBeenCalled();
  });
});

describe("useActions — clearQueue", () => {
  it("reports how many items were removed", async () => {
    mockFetch({ ok: true, body: { cancelled: 4 } });
    await useSubject().clearQueue();

    expect(deps.toast).toHaveBeenCalledWith(
      expect.stringContaining("4 items removed"), "neutral");
  });

  it("uses the singular for exactly one item", async () => {
    mockFetch({ ok: true, body: { cancelled: 1 } });
    await useSubject().clearQueue();

    const [msg] = deps.toast.mock.calls[0];
    expect(msg).toContain("1 item removed");
    expect(msg).not.toContain("1 items");
  });

  it("says the queue was already empty when nothing was cancelled", async () => {
    mockFetch({ ok: true, body: { cancelled: 0 } });
    await useSubject().clearQueue();

    expect(deps.toast).toHaveBeenCalledWith("Queue is already empty", "neutral");
  });

  it("invalidates the Failed tab, where cancelled items land", async () => {
    mockFetch({ ok: true, body: { cancelled: 2 } });
    await useSubject().clearQueue();

    expect(deps.invalidateHistory).toHaveBeenCalledWith("failed");
  });
});

/* ── Bulk retry counts ──────────────────────────────────────────────────── */

describe("useActions — retryAllFailed", () => {
  it("reports every non-zero count", async () => {
    mockFetch({ ok: true, body: { retried: 3, skipped: 2, manual_review: 1, errors: [] } });
    await useSubject().retryAllFailed();

    const [msg] = deps.toast.mock.calls[0];
    expect(msg).toContain("3 requeued");
    expect(msg).toContain("2 skipped");
    expect(msg).toContain("1 need review");
  });

  it("surfaces items needing review separately, not folded into another count", async () => {
    // These are not done — they are waiting on the user in the Review tab.
    mockFetch({ ok: true, body: { retried: 0, skipped: 0, manual_review: 5, errors: [] } });
    await useSubject().retryAllFailed();

    expect(deps.toast.mock.calls[0][0]).toContain("5 need review");
  });

  it("reports errored items with a count", async () => {
    mockFetch({ ok: true, body: { retried: 1, skipped: 0, manual_review: 0, errors: ["a", "b"] } });
    await useSubject().retryAllFailed();

    expect(deps.toast.mock.calls[0][0]).toContain("2 errored");
  });

  it("omits zero counts rather than listing them", async () => {
    mockFetch({ ok: true, body: { retried: 3, skipped: 0, manual_review: 0, errors: [] } });
    await useSubject().retryAllFailed();

    const [msg] = deps.toast.mock.calls[0];
    expect(msg).toContain("3 requeued");
    expect(msg).not.toContain("skipped");
    expect(msg).not.toContain("need review");
  });

  it("says there was nothing to retry when every count is zero", async () => {
    mockFetch({ ok: true, body: { retried: 0, skipped: 0, manual_review: 0, errors: [] } });
    await useSubject().retryAllFailed();

    expect(deps.toast).toHaveBeenCalledWith("No failed items to retry", "neutral");
  });

  it("only invalidates history when something was actually requeued", async () => {
    // Nothing was removed from the Failed tab, so there is nothing to refresh.
    mockFetch({ ok: true, body: { retried: 0, skipped: 4, manual_review: 0, errors: [] } });
    await useSubject().retryAllFailed();

    expect(deps.invalidateHistory).not.toHaveBeenCalled();
  });

  it("invalidates when items were requeued", async () => {
    mockFetch({ ok: true, body: { retried: 2, skipped: 0, manual_review: 0, errors: [] } });
    await useSubject().retryAllFailed();

    expect(deps.invalidateHistory).toHaveBeenCalledWith("failed");
  });
});

/* ── The auto-start interlock ───────────────────────────────────────────── */

describe("useActions — toggleAutoStart", () => {
  /* Found by re-running this file's own mutation set: removing the rollback
   * from toggleAutoStart survived every test above. toggleDryRun's identical
   * interlock was covered four ways over and this one not at all — the two sit
   * ten lines apart and do the same optimistic-update-then-roll-back dance.
   *
   * It is the same class of failure and nearly as serious. Auto-start decides
   * whether the queue processes on its own. If the header shows it OFF while
   * the backend still has it ON, the user believes nothing will run and the
   * worker keeps remuxing — the mirror image of the dry-run bug, and equally
   * silent, since a failed PUT that leaves the UI flipped looks like success.
   */
  it("sends the TOGGLED value, not the current one", async () => {
    mockFetch({ ok: true });
    await useSubject({ autoStart: true }).toggleAutoStart();

    expect(JSON.parse(fetchCalls[0].body)).toEqual({ value: false });
    expect(fetchCalls[0].url).toContain("/api/settings/auto_start_jobs");
  });

  it("turns auto-start on when it was off", async () => {
    mockFetch({ ok: true });
    await useSubject({ autoStart: false }).toggleAutoStart();

    expect(JSON.parse(fetchCalls[0].body)).toEqual({ value: true });
    expect(deps.setAutoStart).toHaveBeenLastCalledWith(true);
  });

  it("rolls the toggle back when the request fails", async () => {
    mockFetch({ ok: false });
    await useSubject({ autoStart: true }).toggleAutoStart();

    expect(deps.setAutoStart).toHaveBeenNthCalledWith(1, false);  // optimistic
    expect(deps.setAutoStart).toHaveBeenNthCalledWith(2, true);   // rolled back
    expect(deps.setAutoStart).toHaveBeenLastCalledWith(true);
  });

  it("rolls back on a network error too", async () => {
    mockFetch({ throws: true });
    await useSubject({ autoStart: true }).toggleAutoStart();

    expect(deps.setAutoStart).toHaveBeenLastCalledWith(true);
  });

  it("reports the failure loudly rather than silently", async () => {
    mockFetch({ ok: false });
    await useSubject({ autoStart: true }).toggleAutoStart();

    const [msg, tone] = deps.toast.mock.calls[0];
    expect(tone).toBe("error");
    expect(msg).toMatch(/auto-start/i);
  });

  it("does not claim success when the request failed", async () => {
    mockFetch({ ok: false });
    await useSubject({ autoStart: true }).toggleAutoStart();

    expect(deps.toast).toHaveBeenCalledTimes(1);
    expect(deps.toast.mock.calls[0][0]).not.toMatch(/disabled|enabled/i);
  });
});
