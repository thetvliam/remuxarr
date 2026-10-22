import { useState, useEffect, useCallback } from "react";
import { DEFAULT_API } from "../constants";
import { basename } from "../utils";
import { useWebSocket } from "./useWebSocket";
import { useBreakpoint } from "./useBreakpoint";
import { useRouting } from "./useRouting";

// How long a toast stays up, and how long a failed revert's stays: its
// reason runs to a sentence or two and is recorded nowhere else.
export const TOAST_MS = 5000;
export const REVERT_FAILURE_TOAST_MS = 15000;

/* ═══════════════════════════════════════════════════════════════════════════
 *  useAppData
 *  Owns all server-derived state, the WebSocket connection, and the data
 *  fetching functions (fetchAll, fetchForge). This is the single source of
 *  truth the rest of the app reads from — components and useActions consume
 *  the values this hook returns rather than managing their own copies.
 *
 *  Routing (the page, the detail modal, and the Back button) is useRouting's.
 *  This hook calls it and returns its values alongside its own, so callers
 *  read both from here; useRouting says why the two are apart.
 ═══════════════════════════════════════════════════════════════════════════ */
export function useAppData() {
  // Called first, so the routing effects run ahead of this hook's own, the
  // order they had when they were written inline here.
  const { page, setPage, registerNavGuard, leaveGuarded, modal, setModal, enrichModal } = useRouting();

  const [api,        setApi]        = useState(DEFAULT_API);
  const [activeJobs, setActiveJobs] = useState([]);
  const [queue,      setQueue]      = useState([]);
  /* Both halves of the Review tab's badge come from /api/queue/stats as
   * counts, not lists. The review page pages by card and the two language
   * sections page by row, so no list here would be the whole number anyway,
   * and the badge only ever needs the number.
   *
   * The language flags have to be included: a file can carry one without
   * ever entering manual review (one undefined audio track under a threshold
   * of two, an undefined subtitle that gets extracted), so the review count
   * alone reads zero while the page below has rows waiting. */
  const [manualReviewCount, setManualReviewCount] = useState(0);
  const [languageReviewCount, setLanguageReviewCount] = useState(0);
  const [toasts,     setToasts]     = useState([]);
  const [dryRun,     setDryRun]     = useState(false);
  const [scanning,   setScanning]   = useState(false);
  const [scanProgress, setScanProgress] = useState(null); // {scanned, total} | null
  const [showApiBar, setShowApiBar] = useState(false);
  const [workerPaused, setWorkerPaused] = useState(false);
  const [autoStart,    setAutoStart]    = useState(true);
  // Incremented whenever history content may have changed — HistoryPanel
  // watches this to know when to reset pagination and re-fetch from page 1.
  // `status` records which specific status just changed (e.g. "success",
  // "failed", "skipped") so a tab that has nothing to do with that status
  // can skip the refetch entirely — otherwise every single job completion
  // would blank and reload every tab, not just the one it actually affects.
  // `status: null` means "could affect anything" (used for scan/cleanup,
  // which can touch multiple statuses at once) — every tab refreshes then.
  const [historyRefreshKey, setHistoryRefreshKey] = useState({ key: 0, status: null });

  /**
   * Mark History as stale.
   *
   * Exists because the raw
   *   setHistoryRefreshKey(prev => ({ key: prev.key + 1, status: X }))
   * incantation was written out by hand at every call site that might have
   * invalidated the panel — and four sites that needed it did not have it
   * (dismissItem, clearQueue, dismissQueueItem, ReviewPage.approve), so a row
   * the user had just dismissed or cancelled stayed on screen until something
   * unrelated triggered a refresh.
   *
   * fetchAll() does NOT cover this: it refetches active jobs, queue,
   * manual-review, worker and scan status, and deliberately never touches
   * history, which paginates separately via useHistoryData.
   *
   * @param status  Which status just changed, so a tab unrelated to it can skip
   *                the refetch. Pass null (the default) when the change could
   *                affect more than one tab, or when the item's status is not
   *                known at the call site — null always refreshes.
   */
  const invalidateHistory = useCallback((status = null) => {
    setHistoryRefreshKey(prev => ({ key: prev.key + 1, status }));
  }, []);

  // Incremented whenever the audio/subtitle language flag tables may have
  // changed — the Review page's two language sections fetch their own
  // paginated lists and had no way to learn that.
  //
  // Those flags are written inside _process_file, so they change on a scan,
  // on a webhook-queued file, and on a job finishing. None of those bumped
  // anything the sections were watching: each owned a private refreshKey it
  // incremented only after its OWN actions. A scan could therefore surface
  // twenty new mismatches and the section would keep showing the list it
  // fetched on mount until the page was navigated away from and back.
  //
  // Deliberately not bumped on job_progress or scan_progress — those fire
  // continuously and would refetch the list on every tick.
  const [reviewRefreshKey, setReviewRefreshKey] = useState(0);
  const [revertRefreshKey, setRevertRefreshKey] = useState(0);

  // ── Forge tab state ──────────────────────────────────────────────────────
  const [forgeActive,    setForgeActive]    = useState(null);
  const [forgeProcessed, setForgeProcessed] = useState([]);
  // Incremented whenever the candidates list may have changed — triggers
  // usePaginatedFetch to reset and re-fetch in CandidatesPanel.
  const [forgeRefreshKey, setForgeRefreshKey] = useState(0);

  /* ── Toast helper ─────────────────────────────────────────────────────── */
  /* The second argument is a TONE NAME — "error", "success", "notice" — not a
   * colour. Toasts resolves it against the theme at render.
   *
   * The parameter and the stored key have to agree with what Toasts reads.
   * They did not: callers were updated to pass tone names and the renderer
   * was updated to read `tone`, but this function kept storing the value
   * under `color`. `t.tone` was therefore undefined on every toast and every
   * one fell back to the accent, so a failed job and a successful save
   * looked identical. Nothing failed loudly — a lookup miss just returns the
   * fallback, which is a real colour.
   *
   * The value is only ever a key into toastTone, so passing a hex here
   * degrades to the fallback rather than rendering that colour.
   *
   * The third argument is how long the toast stays, in milliseconds. Five
   * seconds suits a status line; a failure whose reason is the only record
   * of what went wrong gets longer — see revert_complete. */
  const toast = useCallback((msg, tone, durationMs = TOAST_MS) => {
    const id = Date.now() + Math.random();
    setToasts(t => [...t, { id, msg, tone }].slice(-8));
    const isOtherToast = (x) => x.id !== id;
    const dismiss = () => setToasts(t => t.filter(isOtherToast));
    setTimeout(dismiss, durationMs);
  }, []);

  /* ── Data fetching ────────────────────────────────────────────────────── */
  const fetchAll = useCallback(async () => {
    // dry_run_mode is fetched here, alongside auto_start_jobs, rather than
    // only once on mount. Both are written from outside this hook — the
    // header toggles either, Settings writes both, and abort_job clears
    // auto_start_jobs server-side — but only auto_start_jobs was refreshed,
    // so the dry-run badge could disagree with the backend for an entire
    // session. Worse, the next header click computes `!dryRun` from the
    // stale value, so it can write back the state that is already set and
    // then toast whichever the client believed. toggleDryRun's own comment
    // calls that out as "the difference between a preview and an
    // irreversible write" for the failure path; staleness produced the same
    // visible outcome by another route.
    const [a, q, w, s, sc, dr, st] = await Promise.allSettled([
      fetch(`${api}/api/queue/active`).then(r => r.json()),
                                                             fetch(`${api}/api/queue/`).then(r => r.json()),
                                                             fetch(`${api}/api/worker/status`).then(r => r.json()),
                                                             fetch(`${api}/api/settings/auto_start_jobs`).then(r => r.json()),
                                                             fetch(`${api}/api/scan/status`).then(r => r.json()),
                                                             fetch(`${api}/api/settings/dry_run_mode`).then(r => r.json()),
                                                             fetch(`${api}/api/queue/stats`).then(r => r.json()),
    ]);
    if (a.status  === "fulfilled") setActiveJobs(Array.isArray(a.value) ? a.value : []);
    if (q.status  === "fulfilled") setQueue(Array.isArray(q.value) ? q.value : []);
    if (w.status  === "fulfilled") setWorkerPaused(w.value?.paused ?? false);
    if (s.status  === "fulfilled") setAutoStart(s.value?.value ?? true);
    if (dr.status === "fulfilled") setDryRun(!!dr.value?.value);
    /* Audio and subtitle backlogs are summed here rather than kept apart:
     * the badge is one number, and the two sections are two views of the
     * same "languages still to answer" pile. A dropped poll leaves the
     * previous value alone, like every other branch here — st is simply
     * not fulfilled and nothing below runs.
     *
     * Optional chaining all the way down rather than an `if (lr)` guard,
     * which reads as equivalent and is not. A guard turns a response
     * without the key into a CRASH once removed, and a crash inside this
     * floating promise surfaces as an unhandled rejection: the run goes
     * red while every test still passes, so no assertion is actually
     * holding the behaviour. Written this way, the same mistake produces
     * NaN instead — a wrong number the badge test can catch. */
    if (st.status === "fulfilled") {
      const lr = st.value?.language_review;
      setLanguageReviewCount((lr?.audio ?? 0) + (lr?.subtitle ?? 0));
      /* Counted in SQL by queue_stats rather than read off a list: the list
       * this used to come from is gone, and the number is all the badge
       * wanted from it. */
      setManualReviewCount(st.value?.manual_review ?? 0);
    }
    if (sc.status === "fulfilled") {
      setScanning(sc.value?.running ?? false);
      if (sc.value?.running && sc.value?.total > 0) {
        setScanProgress({ scanned: sc.value.scanned, total: sc.value.total });
      } else if (!sc.value?.running) {
        setScanProgress(null);
      }
    }
  }, [api]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const fetchForge = useCallback(async () => {
    /* Status-checked rather than shape-checked, unlike fetchAll above, and
     * the difference is forced by what these two endpoints return.
     *
     * fetchAll can guard on shape because every one of its responses has a
     * shape an error body fails: Array.isArray for the lists, `?.value ??`
     * for the scalars. /api/forge/active returns an object or null, and a
     * FastAPI error body is also an object — so no shape test tells them
     * apart, and setForgeActive(a.value) stored the error as a job.
     * ForgeActivePanel treats any truthy value as work in progress, so that
     * rendered FORGING with a pulsing LED, "Unknown file" and a bar at 0.0%,
     * and nothing polls this to correct it.
     *
     * allSettled then does the rest: a rejected request skips its setter, so
     * a failed refresh leaves the panel showing the last good answer instead
     * of a phantom job or an emptied list. The Array.isArray guard below
     * stays for a 200 that is not a list, which the status check cannot
     * catch. */
    const readJson = async (path) => {
      const r = await fetch(`${api}${path}`);
      if (!r.ok) throw new Error(`HTTP ${r.status} from ${path}`);
      return r.json();
    };
    const [a, p] = await Promise.allSettled([
      readJson("/api/forge/active"),
      readJson("/api/forge/processed/"),
    ]);
    if (a.status === "fulfilled") setForgeActive(a.value);
    if (p.status === "fulfilled") setForgeProcessed(Array.isArray(p.value) ? p.value : []);
  }, [api]);

  /**
   * Refresh every panel that reads scan state, for a change that invalidated
   * all of them at once.
   *
   * Exists for the same reason invalidateHistory does. scan_completed and
   * cleanup_completed each write out fetchAll + invalidateHistory(null) +
   * setReviewRefreshKey by hand, and that is exactly the shape that goes
   * wrong when it is copied to a new call site minus whichever line the
   * author did not think about.
   *
   * Wider than either of those, because clearing the database also empties
   * forge_jobs and revert_points — so the forge and revert panels have to be
   * told too, and neither of their refresh keys is otherwise reachable from
   * outside this hook.
   *
   * Deliberately NOT retrofitted onto scan_completed and cleanup_completed:
   * they refresh less because they invalidate less, and widening them is a
   * separate question from wiring up a caller that needs all of it.
   */
  /**
   * A manual-review item was resolved: approved, skipped, or had its
   * subtitles decided.
   *
   * Narrower than refreshAllPanels on purpose — resolving one item does not
   * touch forge_jobs or revert_points, and refetching those would be work
   * nothing asked for.
   *
   * The reviewRefreshKey bump is the part that was missing. Resolving calls
   * _apply_decision_to_item, which calls _upsert_language_flags for both the
   * skipped and pending outcomes — deliberately, so a mismatch the engine
   * noticed while re-deciding is not lost. That row belongs to a section on
   * the same page, driven by usePaginatedFetch, which watches this key and
   * nothing else. Without the bump the row existed and the page did not move.
   *
   * Exists rather than exporting setReviewRefreshKey for callers to combine
   * themselves, for the reason recorded on refreshAllPanels below: ReviewPage
   * had fetchAll + invalidateHistory(null) written out by hand at four call
   * sites, and all four were missing the same third line.
   */
  const refreshAfterReviewResolved = useCallback(() => {
    fetchAll();
    invalidateHistory(null);
    setReviewRefreshKey(k => k + 1);
  }, [fetchAll, invalidateHistory]);

  const refreshAllPanels = useCallback(() => {
    fetchAll();
    fetchForge();
    invalidateHistory(null);
    setReviewRefreshKey(k => k + 1);
    setRevertRefreshKey(k => k + 1);
    setForgeRefreshKey(k => k + 1);
  }, [fetchAll, fetchForge, invalidateHistory]);

    useEffect(() => {
      if (page === "forge") fetchForge();
    }, [page, fetchForge]);


      useEffect(() => {
        if (!scanning) return;
        const id = setInterval(() => {
          fetch(`${api}/api/scan/status`)
          .then(r => r.json())
          .then(d => { if (!d.running) setScanning(false); })
          .catch(() => {});
        }, 3000);
        return () => clearInterval(id);
      }, [scanning, api]);

      /* ── WebSocket event handler ──────────────────────────────────────────── */
      const onWsMsg = useCallback((msg) => {
        switch (msg.event) {
          case "job_started":
            fetchAll();
            break;

          case "job_progress":
            setActiveJobs(prev =>
            prev.map(j =>
            j.id === msg.job_id
            ? { ...j, progress: msg.progress, current_action: msg.current_action }
            : j
            )
            );
            setQueue(prev =>
            prev.map(i =>
            i.id === msg.job_id
            ? { ...i, progress: msg.progress, status: "processing" }
            : i
            )
            );
            break;

          case "job_completed":
            /* The refreshes run before the toast, and msg.status is guarded
             * the way forge_job_completed already guards it.
             *
             * msg.status.toUpperCase() was unguarded and was the first thing
             * this case evaluated. useWebSocket invokes this callback inside
             * a bare catch, so a message arriving without a status threw,
             * was swallowed, and took the whole branch with it — no toast,
             * no fetchAll, no history or review refresh. The dashboard
             * simply stopped tracking that job, with nothing logged.
             *
             * Ordering matters as much as the guard: the state updates are
             * what keep the UI correct, so they must not sit behind a
             * cosmetic string operation that can throw. */
            fetchAll();
            invalidateHistory(msg.status ?? null);
            setReviewRefreshKey(k => k + 1);
            /* A successful job is the only thing that CREATES a revert
             * point, and fetchAll does not cover the recycle bin — it has
             * its own endpoint and its own key. Without this the panel
             * only ever shrank: reverting refreshed it, processing did
             * not, so entries appeared out of nowhere on the next manual
             * reload. Bumped for every outcome rather than successes
             * only, because a failed job can still have written and then
             * discarded a sidecar, and the panel should reflect whatever
             * is actually there. */
            setRevertRefreshKey(k => k + 1);
            toast(
              msg.status === "dry_run"
              ? `${msg.filename || "File"} — DRY RUN PREVIEW READY`
              : `${msg.filename || "File"} — ${(msg.status || "unknown").toUpperCase()}` +
              (msg.error ? `: ${msg.error.slice(0, 55)}` : ""),
                  msg.status === "success" ? "success"
                  : msg.status === "dry_run" ? "preview"
                  : "error",
            );
            break;

          case "revert_complete":
            /* A revert returns from its POST as soon as it has STARTED, so
             * the panel that requested it refreshes against a file that is
             * still being rewritten and shows the entry as though nothing
             * happened. This is the only signal that it finished.
             *
             * The key bump is what the Recycle Bin panel reloads on. The
             * toast carries the outcome because the panel may not even be
             * on screen by the time a large file finishes. */
            setRevertRefreshKey(k => k + 1);
            /* A failure's reason is shown whole and left up longer. It is
             * recorded nowhere else — the entry just goes back to offering
             * Revert — and the reasons run to a sentence or two ("Another
             * file already exists as …, and reverting would overwrite it"),
             * so the 60 characters it used to be cut to rarely reached the
             * part that said what to do. Toasts wrap, so length is no
             * problem for the layout. */
            if (msg.success) {
              toast(`Reverted: ${basename(msg.restored_path || "")}`, "success");
            } else {
              toast(`Revert failed${msg.error ? `: ${String(msg.error)}` : ""}`,
                    "error", REVERT_FAILURE_TOAST_MS);
            }
            break;

          case "file_queued":
            toast(`Queued: ${basename(msg.file_path)}`, "info");
            fetchAll();
            // A webhook-queued file goes through _process_file like any
            // scanned one, so it can raise a language flag too.
            setReviewRefreshKey(k => k + 1);
            break;

          case "scan_started":
            setScanning(true);
            setScanProgress(null);
            break;

          case "scan_progress":
            setScanProgress({ scanned: msg.scanned, total: msg.total });
            fetch(`${api}/api/queue/`).then(r => r.json())
            .then(d => { if (Array.isArray(d)) setQueue(d); })
            .catch(() => {});
            break;

          case "scan_completed":
            setScanning(false);
            setScanProgress(null);
            toast(
              (msg.cancelled ? "Scan stopped — " : "Scan complete — ") +
              `${msg.queued} queued, ${msg.manual_review} review, ${msg.errors} errors` +
              (msg.removed ? `, ${msg.removed} removed` : "") +
              (msg.excluded ? `, ${msg.excluded} excluded` : ""),
                  "notice",
            );
            fetchAll();
            invalidateHistory(null);
            setReviewRefreshKey(k => k + 1);
            break;

          case "cleanup_completed":
            toast(
              msg.removed === 0
              ? "Cleanup complete — no stale entries found"
              : `Cleanup complete — ${msg.removed} stale ${msg.removed === 1 ? "entry" : "entries"} removed`,
              "info",
            );
            fetchAll();
            invalidateHistory(null);
            setReviewRefreshKey(k => k + 1);
            break;

          case "forge_job_started":
            fetchForge();
            setForgeRefreshKey(k => k + 1);
            break;
          case "forge_job_progress":
            setForgeActive(prev =>
            prev?.id === msg.job_id
            ? { ...prev, progress: msg.progress, current_action: msg.current_action }
            : prev
            );
            break;
          case "forge_job_completed":
            /* Refreshes first, for the reason job_completed sets out above:
             * useWebSocket invokes this callback inside a bare catch, so
             * anything that throws here is swallowed along with the rest of
             * the branch. msg.status is guarded, but msg.error is not —
             * .slice() on a non-string throws, and with the toast first that
             * took fetchForge and the key bump with it, leaving the forge
             * panel showing a job that had already finished.
             *
             * The backend only ever sends job.error_message, a string or
             * None, so this is not currently reachable. It is ordered this
             * way because the state updates are the part that has to survive,
             * not because the throw is expected. */
            fetchForge();
            setForgeRefreshKey(k => k + 1);
            toast(
              `Forge: ${msg.filename || "file"} — ${(msg.status || "").toUpperCase()}` +
              (msg.error ? `: ${String(msg.error).slice(0, 50)}` : ""),
                  msg.status === "success" ? "success"
                  : msg.status === "undone" ? "info"
                  : "error",
            );
            break;
        }
        /* No theme value appears in this callback any more — the toasts it
         * raises name a tone, and the colour is resolved by Toasts at render.
         * That is the point of the change: a dependency array cannot go stale
         * on a value it never captures. `api` stays because the body reads it
         * directly; it was missing before, masked only by `fetchAll` happening
         * to close over the same value.
         *
         * invalidateHistory is a useCallback with an empty dep array, so it is
         * referentially stable and adding it here does not cause this callback
         * to be rebuilt on every render — it is listed because the rule is set
         * to error and it caught this omission when the raw
         * setHistoryRefreshKey calls were routed through the helper. */
      }, [fetchAll, fetchForge, toast, api, invalidateHistory]);

      const wsUrl       = api.replace(/^http/, "ws") + "/ws";
      const wsConnected = useWebSocket(wsUrl, onWsMsg, fetchAll);
      const { isMobile, isCompact } = useBreakpoint();

      const pendingQueue = queue.filter(i => i.status !== "processing");

      /* What the Review tab badge shows. Derived here rather than in App.jsx
       * so the hook's own tests can pin it without rendering the whole app,
       * as this sum is the whole point of fetching the stats. App.test.jsx
       * checks that it reaches the tab.
       *
       * It is the sum of the Review page's section counts, so the number on
       * the tab is the number of things waiting there. It is not a count of
       * files: language flags are per track, and a file in manual review
       * keeps the language flags it already had, so one file can add to
       * both terms. See queue_stats' docstring. */
      const reviewBadgeCount = manualReviewCount + languageReviewCount;

      return {
        api, setApi, page, setPage,
        registerNavGuard, leaveGuarded,
        activeJobs, queue, languageReviewCount, reviewBadgeCount,
        modal, setModal, enrichModal,
        toasts,
        dryRun, setDryRun,
        scanning, setScanning, scanProgress,
        showApiBar, setShowApiBar,
        workerPaused, setWorkerPaused,
        autoStart, setAutoStart,
        historyRefreshKey, invalidateHistory,
        reviewRefreshKey, refreshAfterReviewResolved,
        revertRefreshKey,
        forgeActive, forgeProcessed, forgeRefreshKey, setForgeRefreshKey,
          toast, fetchAll, fetchForge, refreshAllPanels,
          pendingQueue, wsConnected, isMobile, isCompact,
      };
}
