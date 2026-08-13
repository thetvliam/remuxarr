import { useState, useEffect, useCallback, useRef } from "react";
import { DEFAULT_API } from "../constants";
import { basename } from "../utils";
import { useWebSocket } from "./useWebSocket";
import { useBreakpoint } from "./useBreakpoint";

/* ── Routing helpers ──────────────────────────────────────────────────────── */

const VALID_PAGES = new Set(["dashboard", "settings", "review", "forge"]);

// Read the current page from the URL hash.
// Falls back to "dashboard" for any unknown or missing hash so the app
// always lands somewhere sensible on a direct visit or a stale bookmark.
const _pageFromHash = () => {
  const hash = window.location.hash.slice(1); // strip leading #
  return VALID_PAGES.has(hash) ? hash : "dashboard";
};

/* ═══════════════════════════════════════════════════════════════════════════
 *  useAppData
 *  Owns all server-derived state, the WebSocket connection, and the data
 *  fetching functions (fetchAll, fetchForge). This is the single source of
 *  truth the rest of the app reads from — components and useActions consume
 *  the values this hook returns rather than managing their own copies.
 *
 *  Client-side routing is implemented here via the browser History API.
 *  Two pieces of state contribute history entries:
 *
 *  • Page navigation  →  #dashboard, #settings, #review, #forge
 *  • Modal open/close →  same URL, different state object ({ modal: true })
 *
 *  Wrapping setPage and setModal here means every caller (AppHeader,
 *  useActions, App.jsx) gets correct back-button behaviour automatically —
 *  nothing else in the codebase needs to change.
 * ═ *══════════════════════════════════════════════════════════════════════════ */
export function useAppData() {
  // ── Routing refs ──────────────────────────────────────────────────────────
  // pageRef mirrors the `page` state value synchronously so setModal can
  // read the current page without a stale closure.
  const pageRef         = useRef(_pageFromHash());
  // modalRef mirrors the `modal` state value synchronously so setModal can
  // detect whether a modal is already open (avoids pushing duplicate history
  // entries when openDetail enriches the modal data with a second setModal).
  const modalRef        = useRef(null);
  // closedByUserRef is a one-shot flag: set true when setModal(null) triggers
  // history.back() programmatically so the resulting popstate event knows the
  // modal was already closed and doesn't try to close it a second time.
  const closedByUserRef = useRef(false);

  const [api,        setApi]        = useState(DEFAULT_API);
  // Initialize page from the URL hash so direct visits and refreshes land on
  // the correct tab (e.g. http://remuxarr:8080/#settings → Settings tab).
  const [page,       setPageState]  = useState(_pageFromHash);
  const [activeJobs, setActiveJobs] = useState([]);
  const [queue,      setQueue]      = useState([]);
  const [review,     setReview]     = useState([]);
  const [modal,      setModalState] = useState(null);
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

  // ── Forge tab state ──────────────────────────────────────────────────────
  const [forgeActive,    setForgeActive]    = useState(null);
  const [forgeProcessed, setForgeProcessed] = useState([]);
  // Incremented whenever the candidates list may have changed — triggers
  // usePaginatedFetch to reset and re-fetch in CandidatesPanel.
  const [forgeRefreshKey, setForgeRefreshKey] = useState(0);

  /* ── Routing: initial replaceState ───────────────────────────────────────
   *    Replace the browser's very first history entry with a state object so
   *    that pressing Back to the initial entry gives event.state = { page, modal }
   *    rather than null (which would prevent us from restoring the correct page). */
  useEffect(() => {
    const initial = _pageFromHash();
    pageRef.current = initial;
    setPageState(initial);
    window.history.replaceState(
      { page: initial, modal: false },
      "",
      `#${initial}`,
    );
  }, []);

  /* ── Routing: popstate (browser/Android back button) ─────────────────────
   *    Handles two cases:
   *    1. Back from modal state  → close the modal, stay on the same page
   *    2. Back from page navigation → navigate to the previous page            */
  useEffect(() => {
    const handler = (event) => {
      // The closedByUserRef flag is set when setModal(null) calls
      // history.back() programmatically — we've already handled the close
      // in React state, so skip the event to avoid a double update.
      if (closedByUserRef.current) {
        closedByUserRef.current = false;
        return;
      }

      const hasState = event.state != null;
      const state = event.state ?? {};

      /* event.state is the state of the entry being navigated TO, not the
       * one being left. Going back from an open modal therefore lands on
       * the { modal: false } page entry and is handled by the else branch —
       * this if only fires going FORWARD into a modal entry, where it
       * closes the modal rather than reopening it.
       *
       * Behaviour is fine either way, since forward-into-a-modal is not a
       * flow the app produces. The comments said the opposite, which
       * matters because closedByUserRef below is reasoned about in terms of
       * this model. */
      if (state.modal) {
        // Forward navigation INTO a modal entry.
        modalRef.current = null;
        setModalState(null);
      } else {
        // Back to a page entry, including back out of an open modal.
        //
        // Two different fallbacks, deliberately not merged. A history entry
        // the app never created carries NO state object — a manually edited
        // fragment, or an in-page anchor — and for those the URL is the only
        // record of where the user is, so honour it. _pageFromHash() already
        // does exactly this on initial load; the handler simply was not
        // reusing it, so it forced "dashboard" and navigated the app away
        // from the page the URL still named, leaving state and URL
        // disagreeing with every later Back press compounding it.
        //
        // A state object that IS present but names an invalid page is a
        // different situation: the app created that entry, so its page value
        // is authoritative — just stale, most plausibly an entry from a
        // version where that page existed. "dashboard" is the right answer
        // there, and reaching for the URL would be trusting a fragment the
        // same stale entry wrote.
        const target = VALID_PAGES.has(state.page)
        ? state.page
        : (hasState ? "dashboard" : _pageFromHash());
        pageRef.current = target;
        setPageState(target);
        // Also close any open modal — defensive, shouldn't normally be open
        modalRef.current = null;
        setModalState(null);
      }
    };

    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);

  /* ── Routing: wrapped setPage ────────────────────────────────────────────
   *    Called by AppHeader nav tabs. Pushes a new history entry so the back
   *    button can return to the previous tab. */
  const setPage = useCallback((newPage) => {
    // Clicking the tab you are already on pushed another identical entry,
    // so Back then needed one press per click before it did anything
    // visible — the button looked broken rather than slow.
    if (pageRef.current === newPage) return;
    pageRef.current = newPage;
    setPageState(newPage);
    window.history.pushState(
      { page: newPage, modal: false },
      "",
      `#${newPage}`,
    );
  }, []);

  /* ── Routing: wrapped setModal ───────────────────────────────────────────
   *    Handles three cases:
   *    • Opening a new modal  → push a history entry (modal: true)
   *    • Enriching an open modal (openDetail's second fetch) → no push
   *    • Closing the modal    → history.back() removes the modal history entry  */
  const setModal = useCallback((item) => {
    if (item === null) {
      // Only act if a modal is currently open.
      if (modalRef.current !== null) {
        closedByUserRef.current = true; // suppress the upcoming popstate
        modalRef.current = null;
        setModalState(null);
        window.history.back(); // remove the modal history entry
      }
      return;
    }

    const wasOpen = modalRef.current !== null;
    modalRef.current = item;
    setModalState(item);

    // First open only: push a history entry so the back button can close it.
    // When openDetail calls setModal a second time to enrich with full data,
    // wasOpen is true so we skip the push — no duplicate history entry.
    if (!wasOpen) {
      window.history.pushState(
        { page: pageRef.current, modal: true },
        "",
        `#${pageRef.current}`,
      );
    }
  }, []);

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
   * degrades to the fallback rather than rendering that colour. */
  const toast = useCallback((msg, tone) => {
    const id = Date.now() + Math.random();
    setToasts(t => [...t, { id, msg, tone }].slice(-8));
    const isOtherToast = (x) => x.id !== id;
    const dismiss = () => setToasts(t => t.filter(isOtherToast));
    setTimeout(dismiss, 5000);
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
    const [a, q, r, w, s, sc, dr] = await Promise.allSettled([
      fetch(`${api}/api/queue/active`).then(r => r.json()),
                                                             fetch(`${api}/api/queue/`).then(r => r.json()),
                                                             fetch(`${api}/api/queue/manual-review`).then(r => r.json()),
                                                             fetch(`${api}/api/worker/status`).then(r => r.json()),
                                                             fetch(`${api}/api/settings/auto_start_jobs`).then(r => r.json()),
                                                             fetch(`${api}/api/scan/status`).then(r => r.json()),
                                                             fetch(`${api}/api/settings/dry_run_mode`).then(r => r.json()),
    ]);
    if (a.status  === "fulfilled") setActiveJobs(Array.isArray(a.value) ? a.value : []);
    if (q.status  === "fulfilled") setQueue(Array.isArray(q.value) ? q.value : []);
    if (r.status  === "fulfilled") setReview(Array.isArray(r.value) ? r.value : []);
    if (w.status  === "fulfilled") setWorkerPaused(w.value?.paused ?? false);
    if (s.status  === "fulfilled") setAutoStart(s.value?.value ?? true);
    if (dr.status === "fulfilled") setDryRun(!!dr.value?.value);
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
    const [a, p] = await Promise.allSettled([
      fetch(`${api}/api/forge/active`).then(r => r.json()),
                                            fetch(`${api}/api/forge/processed/`).then(r => r.json()),
    ]);
    if (a.status === "fulfilled") setForgeActive(a.value);
    if (p.status === "fulfilled") setForgeProcessed(Array.isArray(p.value) ? p.value : []);
  }, [api]);

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
              (msg.removed ? `, ${msg.removed} removed` : ""),
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
            toast(
              `Forge: ${msg.filename || "file"} — ${(msg.status || "").toUpperCase()}` +
              (msg.error ? `: ${msg.error.slice(0, 50)}` : ""),
                  msg.status === "success" ? "success"
                  : msg.status === "undone" ? "info"
                  : "error",
            );
            fetchForge();
            setForgeRefreshKey(k => k + 1);
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
      const { isMobile } = useBreakpoint();

      const pendingQueue = queue.filter(i => i.status !== "processing");

      return {
        api, setApi, page, setPage,
        activeJobs, queue, review,
        modal, setModal,
        toasts,
        dryRun, setDryRun,
        scanning, setScanning, scanProgress,
        showApiBar, setShowApiBar,
        workerPaused, setWorkerPaused,
        autoStart, setAutoStart,
        historyRefreshKey, invalidateHistory,
        reviewRefreshKey,
        forgeActive, forgeProcessed, forgeRefreshKey, setForgeRefreshKey,
          toast, fetchAll, fetchForge,
          pendingQueue, wsConnected, isMobile,
      };
}
