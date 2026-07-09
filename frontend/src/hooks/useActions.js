import { C } from "../constants";

/* ═══════════════════════════════════════════════════════════════════════════
 *  useActions
 *  Collection of functions that call the backend API and update state via
 *  the setters passed in from useAppData. Has no state of its own — accepts
 *  the full data bundle returned by useAppData() and destructures what it
 *  needs, so the call site can simply do `useActions(data)`.
 ═ *══════════════════════════════════════════════════════════════════════════ */
export function useActions({
  api,
  dryRun, setDryRun,
  workerPaused, setWorkerPaused,
  autoStart, setAutoStart,
  setScanning,
  setModal,
  toast,
  fetchAll,
  fetchForge,
  setHistoryRefreshKey,
}) {
  const toggleDryRun = async () => {
    const next = !dryRun;
    setDryRun(next);
    await fetch(`${api}/api/settings/dry_run_mode`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: next }),
    }).catch(() => {});
    toast(`Dry run ${next ? "enabled" : "disabled"}`, C.yellow);
  };

  const togglePause = async () => {
    const endpoint = workerPaused ? "resume" : "pause";
    const r = await fetch(`${api}/api/worker/${endpoint}`, { method: "POST" }).catch(() => null);
    if (r?.ok) {
      const next = !workerPaused;
      setWorkerPaused(next);
      toast(next ? "Processing paused" : "Processing resumed", next ? C.yellow : C.green);
    }
  };

  // Cancels the currently-processing job AND disables auto-start in the
  // same call — protects a new user who starts a scan without dry-run,
  // sees the first file about to do something unwanted, and needs the
  // whole queue to stop rather than just skip ahead to the next file.
  const abortJob = async (jobId) => {
    const r = await fetch(`${api}/api/worker/abort/${jobId}`, { method: "POST" }).catch(() => null);
    if (r?.ok) {
      setAutoStart(false);
      toast("Job aborted — auto-start disabled", C.red);
      fetchAll();
    } else {
      toast("Failed to abort job", C.red);
    }
  };

  // Discards every dry-run preview item at once — the gap where a user
  // reviews a dry-run batch, doesn't like what it's about to do, and has
  // no way to clear it without waiting for each file to be re-scanned.
  const clearDryRun = async () => {
    try {
      const r = await fetch(`${api}/api/queue/dry-run`, { method: "DELETE" });
      if (!r.ok) { toast("Failed to clear dry-run previews", C.red); return; }
      const { cleared } = await r.json();
      toast(
        cleared > 0
        ? `Cleared ${cleared} dry-run preview${cleared === 1 ? "" : "s"}`
        : "No dry-run previews to clear",
        C.muted,
      );
      fetchAll();
      // This is a synchronous DELETE with no corresponding WS event (unlike
      // job completions, which arrive asynchronously via job_completed and
      // bump this the same way) — bump it directly here so the Dry Run
      // tab's self-fetching hook re-queries and actually reflects the clear.
      // Tagged with status: "dry_run" so only that tab refreshes — clearing
      // dry-run previews has no effect on success/failed/skipped items.
      setHistoryRefreshKey?.(prev => ({ key: prev.key + 1, status: "dry_run" }));
    } catch (_) {
      toast("Failed to clear dry-run previews", C.red);
    }
  };

  const toggleAutoStart = async () => {
    const next = !autoStart;
    setAutoStart(next);
    await fetch(`${api}/api/settings/auto_start_jobs`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: next }),
    }).catch(() => {});
    toast(`Auto-start ${next ? "enabled" : "disabled"}`, C.dim);
  };

  const triggerScan = async () => {
    setScanning(true);
    const r = await fetch(`${api}/api/scan/trigger`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }).catch(() => null);
    if (!r?.ok) setScanning(false);
    else {
      toast("Library scan started", C.amber);
      // If auto-start is off, the backend will pause the worker after the
      // scan — reflect that immediately in the UI.
      if (!autoStart) setWorkerPaused(true);
    }
  };

  const cancelScan = async () => {
    const r = await fetch(`${api}/api/scan/cancel`, { method: "POST" }).catch(() => null);
    if (r?.ok) {
      toast("Stopping scan…", C.amber);
      // Deliberately not setScanning(false) here — the scan loop takes a
      // moment to actually notice the flag (it's checked once per file,
      // right after whatever file it's currently on finishes) and the
      // eventual scan_completed WS event, now carrying cancelled: true,
      // is what correctly clears scanning/scanProgress once it genuinely
      // stops. Clearing it here early would show "idle" while the scan
      // is, for a brief moment, still actually running.
    } else {
      toast("Failed to stop scan", C.red);
    }
  };

  // Open detail modal — fetch full record (with planned_actions) then show
  const openDetail = (item, endpoint) => {
    setModal(item); // show immediately with basic data
    fetch(`${api}${endpoint}/${item.id}`)
    .then(r => r.json())
    .then(full => setModal(full))
    .catch(() => {}); // keep basic modal if fetch fails
  };

  // Re-queue a failed/cancelled item and close the modal
  const retryItem = async (item) => {
    await fetch(`${api}/api/history/${item.id}/retry`, { method: "POST" }).catch(() => {});
    setModal(null);
    fetchAll();
    // Same reasoning as clearDryRun above: retrying deletes the old failed
    // QueueItem immediately, synchronously, with no WS event of its own —
    // the only event that WOULD eventually fire is job_completed once the
    // retried job finishes, which could be seconds or minutes away, and
    // wouldn't fire at all if the retry lands on success rather than
    // failure. Bump directly so the Failed tab reflects the removal now,
    // regardless of what the retry eventually resolves to.
    setHistoryRefreshKey?.(prev => ({ key: prev.key + 1, status: "failed" }));
    toast(`Re-queued: ${item.file?.filename || "file"}`, C.amber);
  };

  // Remove a completed/failed item from history, resetting it for re-scan
  const dismissItem = async (item) => {
    await fetch(`${api}/api/history/${item.id}/`, { method: "DELETE" }).catch(() => {});
    setModal(null);
    fetchAll();
    toast(`Dismissed: ${item.file?.filename || "file"}`, C.muted);
  };

  // ── Forge actions ─────────────────────────────────────────────────────
  const forgeAdd = async (fileId) => {
    await fetch(`${api}/api/forge/queue/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_id: fileId }),
    }).catch(() => {});
    fetchForge();
  };

  const forgeUndo = async (jobId) => {
    await fetch(`${api}/api/forge/${jobId}/undo/`, { method: "POST" }).catch(() => {});
    fetchForge();
  };

  // Remove a single pending item from the queue inline (no modal needed).
  // The item is cancelled — it will re-appear on the next library scan.
  const dismissQueueItem = async (item) => {
    try {
      await fetch(`${api}/api/queue/${item.id}`, { method: "DELETE" });
      toast(`Removed from queue: ${item.file?.filename || "file"}`, C.muted);
      fetchAll();
    } catch (_) {
      toast("Failed to remove item", C.red);
    }
  };

  // Cancel all pending items at once.  They re-appear on the next scan.
  const clearQueue = async () => {
    try {
      const r = await fetch(`${api}/api/queue/`, { method: "DELETE" });
      if (!r.ok) { toast("Failed to clear queue", C.red); return; }
      const { cancelled } = await r.json();
      toast(
        cancelled > 0
        ? `Queue cleared — ${cancelled} item${cancelled === 1 ? "" : "s"} removed`
        : "Queue is already empty",
        C.muted,
      );
      fetchAll();
    } catch (_) {
      toast("Failed to clear queue", C.red);
    }
  };

  // Move a pending item to the front of the queue.
  const prioritizeItem = async (item) => {
    try {
      const r = await fetch(`${api}/api/queue/${item.id}/prioritize`, { method: "POST" });
      if (!r.ok) { toast("Failed to prioritize item", C.red); return; }
      toast(`Moved to top: ${item.file?.filename || "file"}`, C.amber);
      fetchAll();
    } catch (_) {
      toast("Failed to prioritize item", C.red);
    }
  };

  // Retry all failed and cancelled items in one call
  const retryAllFailed = async () => {
    try {
      const r = await fetch(`${api}/api/queue/retry-all`, { method: "POST" });
      if (!r.ok) { toast("Retry all failed", C.red); return; }
      const { retried, skipped } = await r.json();
      const parts = [];
      if (retried > 0) parts.push(`${retried} requeued`);
      if (skipped > 0) parts.push(`${skipped} skipped (file missing)`);
      toast(
        parts.length ? `Retry all: ${parts.join(", ")}` : "No failed items to retry",
            retried > 0 ? C.amber : C.muted,
      );
      fetchAll();
      // Same reasoning as retryItem above, just for the bulk case — every
      // retried item's old QueueItem is already deleted by the time this
      // response comes back, and nothing else will tell the Failed tab
      // that until (and unless) each one individually completes later.
      if (retried > 0) {
        setHistoryRefreshKey?.(prev => ({ key: prev.key + 1, status: "failed" }));
      }
    } catch (_) {
      toast("Retry all failed", C.red);
    }
  };

  return {
    toggleDryRun, togglePause, toggleAutoStart, triggerScan, cancelScan,
    openDetail, retryItem, dismissItem, retryAllFailed,
    dismissQueueItem, clearQueue, prioritizeItem,
    abortJob, clearDryRun,
    forgeAdd, forgeUndo,
  };
}
