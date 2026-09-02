/* ═══════════════════════════════════════════════════════════════════════════
 *  useActions
 *  Collection of functions that call the backend API and update state via
 *  the setters passed in from useAppData. Has no state of its own — accepts
 *  the full data bundle returned by useAppData() and destructures what it
 *  needs, so the call site can simply do `useActions(data)`.
 ═══════════════════════════════════════════════════════════════════════════ */
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
  invalidateHistory,
  setForgeRefreshKey,
}) {
  /* The optimistic update is rolled back on failure, and the failure is
   * reported loudly. This is the app's safety interlock: if the PUT failed
   * and the toggle stayed on, the header showed ◆ DRY RUN and a toast
   * confirmed it while the backend went on actually remuxing files. Silent
   * failure is unacceptable anywhere, but here it is the difference between
   * a preview and an irreversible write. */
  const toggleDryRun = async () => {
    const next = !dryRun;
    setDryRun(next);
    const r = await fetch(`${api}/api/settings/dry_run_mode`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: next }),
    }).catch(() => null);
    if (!r?.ok) {
      setDryRun(!next);
      toast(`Could not change dry run — still ${next ? "OFF" : "ON"}`, "error");
      return;
    }
    toast(`Dry run ${next ? "enabled" : "disabled"}`, "warning");
  };

  /* Reports the failure, like every other action here. It reported nothing:
   * the button is a toggle whose label is driven by workerPaused, so a POST
   * that 500'd left PAUSE reading PAUSE and produced no toast — identical in
   * every respect to a click that had not registered. The natural response is
   * to click again, which fails again, just as quietly. */
  const togglePause = async () => {
    const endpoint = workerPaused ? "resume" : "pause";
    const r = await fetch(`${api}/api/worker/${endpoint}`, { method: "POST" }).catch(() => null);
    if (!r?.ok) {
      // Says what is still true rather than what failed. Nothing was changed,
      // so the useful half is which state the worker remains in.
      toast(
        `Could not ${endpoint} processing — still ${workerPaused ? "PAUSED" : "RUNNING"}`,
        "error",
      );
      return;
    }
    const next = !workerPaused;
    setWorkerPaused(next);
    toast(next ? "Processing paused" : "Processing resumed", next ? "warning" : "success");
  };

  // Cancels the currently-processing job AND disables auto-start in the
  // same call — protects a new user who starts a scan without dry-run,
  // sees the first file about to do something unwanted, and needs the
  // whole queue to stop rather than just skip ahead to the next file.
  const abortJob = async (jobId) => {
    const r = await fetch(`${api}/api/worker/abort/${jobId}`, { method: "POST" }).catch(() => null);
    if (r?.ok) {
      setAutoStart(false);
      toast("Job aborted — auto-start disabled", "error");
      fetchAll();
    } else {
      toast("Failed to abort job", "error");
    }
  };

  // Discards every dry-run preview item at once — the gap where a user
  // reviews a dry-run batch, doesn't like what it's about to do, and has
  // no way to clear it without waiting for each file to be re-scanned.
  const clearDryRun = async () => {
    try {
      const r = await fetch(`${api}/api/queue/dry-run`, { method: "DELETE" });
      if (!r.ok) { toast("Failed to clear dry-run previews", "error"); return; }
      const { cleared } = await r.json();
      toast(
        cleared > 0
        ? `Cleared ${cleared} dry-run preview${cleared === 1 ? "" : "s"}`
        : "No dry-run previews to clear",
        "neutral",
      );
      fetchAll();
      // This is a synchronous DELETE with no corresponding WS event (unlike
      // job completions, which arrive asynchronously via job_completed and
      // bump this the same way) — bump it directly here so the Dry Run
      // tab's self-fetching hook re-queries and actually reflects the clear.
      // Tagged with status: "dry_run" so only that tab refreshes — clearing
      // dry-run previews has no effect on success/failed/skipped items.
      invalidateHistory?.("dry_run");
    } catch (err) {
        console.error("Clear dry-run previews failed", err);
      toast("Failed to clear dry-run previews", "error");
    }
  };

  const toggleAutoStart = async () => {
    const next = !autoStart;
    setAutoStart(next);
    const r = await fetch(`${api}/api/settings/auto_start_jobs`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: next }),
    }).catch(() => null);
    if (!r?.ok) {
      setAutoStart(!next);
      toast(`Could not change auto-start — still ${next ? "OFF" : "ON"}`, "error");
      return;
    }
    toast(`Auto-start ${next ? "enabled" : "disabled"}`, "quiet");
  };

  /* The failure branch existed only to undo the optimistic spinner. cancelScan
   * directly below reports its failure; starting one did not, so a SCAN click
   * that 500'd flickered the button and stopped, which reads as a scan that
   * found nothing rather than one that never began. */
  const triggerScan = async () => {
    setScanning(true);
    const r = await fetch(`${api}/api/scan/trigger`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }).catch(() => null);
    if (!r?.ok) {
      setScanning(false);
      toast("Failed to start scan", "error");
      return;
    }
    toast("Library scan started", "notice");
    // If auto-start is off, the backend will pause the worker after the
    // scan — reflect that immediately in the UI.
    if (!autoStart) setWorkerPaused(true);
  };

  const cancelScan = async () => {
    const r = await fetch(`${api}/api/scan/cancel`, { method: "POST" }).catch(() => null);
    if (r?.ok) {
      toast("Stopping scan…", "notice");
      // Deliberately not setScanning(false) here — the scan loop takes a
      // moment to actually notice the flag (it's checked once per file,
      // right after whatever file it's currently on finishes) and the
      // eventual scan_completed WS event, now carrying cancelled: true,
      // is what correctly clears scanning/scanProgress once it genuinely
      // stops. Clearing it here early would show "idle" while the scan
      // is, for a brief moment, still actually running.
    } else {
      toast("Failed to stop scan", "error");
    }
  };

  // Open detail modal — fetch full record (with planned_actions) then show
  const openDetail = (item, endpoint) => {
    setModal(item); // show immediately with basic data
    fetch(`${api}${endpoint}/${item.id}`)
    // Without the r.ok check, a 404's JSON error body was passed straight
    // to setModal — so the modal's contents became { detail: "…" }, the
    // filename and planned actions vanished, and planned_actions being
    // undefined left it on "Loading…" forever with no error shown.
    // Falling back to the row data keeps the modal useful.
    .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then(full => setModal(full))
    .catch(() => {}); // keep the basic modal if the detail fetch fails
  };

  // Re-queue a failed/cancelled item and close the modal
  const retryItem = async (item) => {
    const r = await fetch(`${api}/api/history/${item.id}/retry`, { method: "POST" }).catch(() => null);
    if (!r?.ok) {
      toast(`Could not re-queue: ${item.file?.filename || "file"}`, "error");
      return;
    }
    setModal(null);
    fetchAll();
    // Same reasoning as clearDryRun above: retrying deletes the old failed
    // QueueItem immediately, synchronously, with no WS event of its own —
    // the only event that WOULD eventually fire is job_completed once the
    // retried job finishes, which could be seconds or minutes away, and
    // wouldn't fire at all if the retry lands on success rather than
    // failure. Bump directly so the Failed tab reflects the removal now,
    // regardless of what the retry eventually resolves to.
    //
    // Tagged from the ITEM, not hardcoded to "failed". This same handler backs
    // the modal's ▶ PROCESS NOW button for dry_run items, and _retry_with_reprobe
    // deletes the dry-run row outright (preserve_completed_record is only true
    // for success/skipped). eventAffectsTab("failed", "dry_run") is false, so a
    // hardcoded "failed" left the Dry Run tab showing a row that no longer
    // existed.
    invalidateHistory?.(item.status || null);
    toast(`Re-queued: ${item.file?.filename || "file"}`, "notice");
  };

  // Remove a completed/failed item from history, resetting it for re-scan
  const dismissItem = async (item) => {
    const r = await fetch(`${api}/api/history/${item.id}`, { method: "DELETE" }).catch(() => null);
    if (!r?.ok) {
      toast(`Could not dismiss: ${item.file?.filename || "file"}`, "error");
      return;
    }
    setModal(null);
    fetchAll();
    // fetchAll refetches queue/active/manual-review/worker/scan — never
    // history. Without this the row the user just dismissed stayed visible in
    // the History panel until something unrelated triggered a refresh.
    // Tagged from the item since it could have been in any tab.
    invalidateHistory?.(item.status || null);
    toast(`Dismissed: ${item.file?.filename || "file"}`, "neutral");
  };

  // ── Forge actions ─────────────────────────────────────────────────────
  // Reports both outcomes. It previously reported neither: a click on
  // + ADD AC3 that 500'd looked exactly like one that worked, because the
  // only visible consequence either way was a list refresh.
  const forgeAdd = async (fileId) => {
    const r = await fetch(`${api}/api/forge/queue/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_id: fileId }),
    }).catch(() => null);
    if (!r?.ok) {
      toast("Could not add to the forge queue", "error");
      return;
    }
    toast("Added to forge queue", "info");
    fetchForge();
    // fetchForge refreshes the ACTIVE and PROCESSED panels only.
    // CandidatesPanel is self-fetching off forgeRefreshKey, and the file just
    // added is no longer a candidate — get_candidates excludes anything with
    // a pending/processing job. Without this bump the row stayed on screen
    // until something unrelated refreshed it, and clicking it again returned
    // a 400 "already queued" that reads as a broken button.
    setForgeRefreshKey?.(k => k + 1);
  };

  const forgeUndo = async (jobId) => {
    const r = await fetch(`${api}/api/forge/${jobId}/undo/`, { method: "POST" }).catch(() => null);
    if (!r?.ok) {
      toast("Could not undo — the original may no longer be on disk", "error");
      return;
    }
    toast("Undo queued", "info");
    fetchForge();
    // Same reasoning in reverse: an undone file becomes a candidate again
    // once the undo completes, and the candidates list will not notice on its
    // own.
    setForgeRefreshKey?.(k => k + 1);
  };

  // Remove a single pending item from the queue inline (no modal needed).
  // The item is cancelled — it will re-appear on the next library scan.
  const dismissQueueItem = async (item) => {
    // fetch only rejects on a network failure, so the catch alone let a 500
    // through to the success toast.
    try {
      const r = await fetch(`${api}/api/queue/${item.id}`, { method: "DELETE" });
      if (!r.ok) {
        toast("Failed to remove item", "error");
        return;
      }
      toast(`Removed from queue: ${item.file?.filename || "file"}`, "neutral");
      fetchAll();
      // The DELETE sets QueueItem.status = "cancelled", and history.py folds
      // "cancelled" into the Failed tab (useHistoryData.eventAffectsTab is
      // written to handle exactly that mapping). Without this the newly
      // cancelled item never appeared there and the tab badge stayed stale.
      invalidateHistory?.("failed");
    } catch (err) {
        console.error("Remove queue item failed", err);
      toast("Failed to remove item", "error");
    }
  };

  // Cancel all pending items at once.  They re-appear on the next scan.
  const clearQueue = async () => {
    try {
      const r = await fetch(`${api}/api/queue/`, { method: "DELETE" });
      if (!r.ok) { toast("Failed to clear queue", "error"); return; }
      const { cancelled } = await r.json();
      toast(
        cancelled > 0
        ? `Queue cleared — ${cancelled} item${cancelled === 1 ? "" : "s"} removed`
        : "Queue is already empty",
        "neutral",
      );
      fetchAll();
      // Same as dismissQueueItem: these become "cancelled", which the Failed
      // tab shows.
      invalidateHistory?.("failed");
    } catch (err) {
        console.error("Clear queue failed", err);
      toast("Failed to clear queue", "error");
    }
  };

  // Move a pending item to the front of the queue.
  const prioritizeItem = async (item) => {
    try {
      const r = await fetch(`${api}/api/queue/${item.id}/prioritize`, { method: "POST" });
      if (!r.ok) { toast("Failed to prioritize item", "error"); return; }
      toast(`Moved to top: ${item.file?.filename || "file"}`, "notice");
      fetchAll();
    } catch (err) {
        console.error("Prioritize queue item failed", err);
      toast("Failed to prioritize item", "error");
    }
  };

  // Retry all failed and cancelled items in one call
  const retryAllFailed = async () => {
    try {
      const r = await fetch(`${api}/api/queue/retry-all`, { method: "POST" });
      if (!r.ok) { toast("Retry all failed", "error"); return; }
      const { retried, skipped, manual_review: needsReview, errors } = await r.json();
      const parts = [];
      if (retried > 0) parts.push(`${retried} requeued`);
      // No longer "(file missing)". skipped now also covers items the re-run
      // decided need no work — a settings change can make a previously-failed
      // file a legitimate no-op, and calling that a missing file was wrong.
      if (skipped > 0) parts.push(`${skipped} skipped`);
      // Surfaced separately because these are not done: they are waiting on
      // the user in the Review tab, and folding them into either count above
      // hid that entirely.
      if (needsReview > 0) parts.push(`${needsReview} need review`);
      if (errors?.length) {
        console.warn("Retry all — items that errored:", errors);
        parts.push(`${errors.length} errored`);
      }
      toast(
        parts.length ? `Retry all: ${parts.join(", ")}` : "No failed items to retry",
            retried > 0 ? "notice" : "neutral",
      );
      fetchAll();
      // Same reasoning as retryItem above, just for the bulk case — every
      // retried item's old QueueItem is already deleted by the time this
      // response comes back, and nothing else will tell the Failed tab
      // that until (and unless) each one individually completes later.
      if (retried > 0) {
        invalidateHistory?.("failed");
      }
    } catch (err) {
        console.error("Retry-all failed", err);
      toast("Retry all failed", "error");
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
