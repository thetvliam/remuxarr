/**
 * useHistoryData — the tab relevance mapping.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * eventAffectsTab decides whether a history-invalidation event should make a
 * given tab refetch. It MIRRORS a backend filter — history.py's list_history
 * does:
 *
 *     if status == "failed":
 *         query = query.filter(QueueItem.status.in_(["failed", "cancelled"]))
 *
 * so a cancelled job (an Abort, or a queue clear) has to refresh the Failed
 * tab even though the literal status differs. That coupling is the fragile
 * part: the two live in different languages, in different files, and a drift
 * between them shows up as a tab that silently stops refreshing. Nothing
 * fails, nothing logs — a row just sits there stale until someone notices.
 *
 * The comments record four separate bugs from a caller invalidating the wrong
 * thing, which is what invalidateHistory was introduced to prevent. This pins
 * the other half: that the invalidation, once raised, reaches the right tabs.
 */
import { describe, expect, it } from "vitest";

import { eventAffectsTab } from "../useHistoryData";

const TABS = ["all", "failed", "success", "skipped", "dry_run"];

describe("eventAffectsTab", () => {
  it("a null status refreshes every tab", () => {
    // Scan and cleanup events carry no status: they can change anything, so
    // they are deliberately unfiltered.
    for (const tab of TABS) {
      expect(eventAffectsTab(null, tab), tab).toBe(true);
    }
    for (const tab of TABS) {
      expect(eventAffectsTab(undefined, tab), tab).toBe(true);
    }
  });

  it("the 'all' tab refreshes on every status", () => {
    for (const status of ["failed", "success", "skipped", "dry_run", "cancelled"]) {
      expect(eventAffectsTab(status, "all"), status).toBe(true);
    }
  });

  it("a status refreshes its own tab", () => {
    for (const status of ["failed", "success", "skipped", "dry_run"]) {
      expect(eventAffectsTab(status, status), status).toBe(true);
    }
  });

  it("cancelled refreshes the Failed tab, mirroring history.py", () => {
    // The backend's Failed query is status.in_(["failed", "cancelled"]), so a
    // cancelled item IS displayed there. Miss this and aborting a job leaves
    // the Failed tab without the row that just appeared in it.
    expect(eventAffectsTab("cancelled", "failed")).toBe(true);
  });

  it("cancelled does not refresh unrelated tabs", () => {
    for (const tab of ["success", "skipped", "dry_run"]) {
      expect(eventAffectsTab("cancelled", tab), tab).toBe(false);
    }
  });

  it("does not refresh a tab the status cannot appear in", () => {
    // The point of the gating: a success event must not cause the Failed tab
    // to reset its list and flash a loading state for nothing.
    const irrelevant = [
      ["success", "failed"],
      ["failed", "success"],
      ["skipped", "failed"],
      ["dry_run", "success"],
      ["success", "dry_run"],
    ];
    for (const [status, tab] of irrelevant) {
      expect(eventAffectsTab(status, tab), `${status} -> ${tab}`).toBe(false);
    }
  });

  it("is exhaustive over the tab set", () => {
    // Guards against a new tab being added without a mapping decision: every
    // pair must return a boolean, not undefined.
    for (const status of [null, "failed", "success", "skipped", "dry_run", "cancelled"]) {
      for (const tab of TABS) {
        expect(typeof eventAffectsTab(status, tab), `${status} -> ${tab}`).toBe("boolean");
      }
    }
  });
});
