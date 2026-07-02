import { useState, useEffect, useCallback, useRef } from "react";
import { DEFAULT_API, C } from "../constants";
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
 ═ *══════════════════════════════════════════════════════════════════════════ */
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
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);

  // ── Forge tab state ──────────────────────────────────────────────────────
  const [forgeActive,    setForgeActive]    = useState(null);
  const [forgeProcessed, setForgeProcessed] = useState([]);
  // Incremented whenever the candidates list may have changed — triggers
  // useCandidatesData to reset and re-fetch in CandidatesPanel.
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
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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

      const state = event.state ?? {};

      if (state.modal) {
        // Navigating back from the "modal open" history entry → close it
        modalRef.current = null;
        setModalState(null);
      } else {
        // Navigating back from a page history entry → restore that page
        const target = VALID_PAGES.has(state.page) ? state.page : "dashboard";
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

  /* ── Global CSS injection ─────────────────────────────────────────────── */
  useEffect(() => {
    // Google Font
    const link    = document.createElement("link");
    link.rel      = "stylesheet";
    link.href     = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap";
    document.head.appendChild(link);

    // Global resets + keyframes
    const style       = document.createElement("style");
    style.textContent = `
    *, *::before, *::after { box-sizing: border-box; }
    html, body { margin: 0; height: 100%; background: ${C.bg}; }
    ::-webkit-scrollbar        { width: 3px; }
    ::-webkit-scrollbar-track  { background: transparent; }
    ::-webkit-scrollbar-thumb  { background: ${C.border}; }
    @keyframes ledPulse { 0%,100%{opacity:1} 50%{opacity:0.25} }
    @keyframes toastIn  { from{opacity:0;transform:translateX(6px)} to{opacity:1;transform:none} }
    @keyframes modalIn  { from{opacity:0;transform:translateY(-6px)} to{opacity:1;transform:none} }
    `;
    document.head.appendChild(style);
    document.title = "Remuxarr";
  }, []);

  /* ── Toast helper ─────────────────────────────────────────────────────── */
  const toast = useCallback((msg, color) => {
    const id = Date.now() + Math.random();
    setToasts(t => {
      const next = [...t, { id, msg, color }];
      return next.slice(-8);
    });
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 5000);
  }, []);

  /* ── Data fetching ────────────────────────────────────────────────────── */
  const fetchAll = useCallback(async () => {
    const [a, q, r, w, s, sc] = await Promise.allSettled([
      fetch(`${api}/api/queue/active`).then(r => r.json()),
                                                         fetch(`${api}/api/queue`).then(r => r.json()),
                                                         fetch(`${api}/api/queue/manual-review`).then(r => r.json()),
                                                         fetch(`${api}/api/worker/status`).then(r => r.json()),
                                                         fetch(`${api}/api/settings/auto_start_jobs`).then(r => r.json()),
                                                         fetch(`${api}/api/scan/status`).then(r => r.json()),
    ]);
    if (a.status  === "fulfilled") setActiveJobs(Array.isArray(a.value) ? a.value : []);
    if (q.status  === "fulfilled") setQueue(Array.isArray(q.value) ? q.value : []);
    if (r.status  === "fulfilled") setReview(Array.isArray(r.value) ? r.value : []);
    if (w.status  === "fulfilled") setWorkerPaused(w.value?.paused ?? false);
    if (s.status  === "fulfilled") setAutoStart(s.value?.value ?? true);
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

      useEffect(() => {
        fetch(`${api}/api/settings/dry_run_mode`)
        .then(r => r.json())
        .then(d => setDryRun(!!d.value))
        .catch(() => {});
      }, [api]);

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
            toast(
              msg.status === "dry_run"
              ? `${msg.filename || "File"} — DRY RUN PREVIEW READY`
              : `${msg.filename || "File"} — ${msg.status.toUpperCase()}` +
              (msg.error ? `: ${msg.error.slice(0, 55)}` : ""),
                  msg.status === "success" ? C.green
                  : msg.status === "dry_run" ? C.violet
                  : C.red,
            );
            fetchAll();
            setHistoryRefreshKey(k => k + 1);
            break;

          case "file_queued":
            toast(`Queued: ${basename(msg.file_path)}`, C.blue);
            fetchAll();
            break;

          case "scan_started":
            setScanning(true);
            setScanProgress(null);
            break;

          case "scan_progress":
            setScanProgress({ scanned: msg.scanned, total: msg.total });
            fetch(`${api}/api/queue`).then(r => r.json())
            .then(d => { if (Array.isArray(d)) setQueue(d); })
            .catch(() => {});
            break;

          case "scan_completed":
            setScanning(false);
            setScanProgress(null);
            toast(
              `Scan complete — ${msg.queued} queued, ${msg.manual_review} review, ${msg.errors} errors` +
              (msg.removed ? `, ${msg.removed} removed` : ""),
                  C.amber,
            );
            fetchAll();
            setHistoryRefreshKey(k => k + 1);
            break;

          case "cleanup_completed":
            toast(
              msg.removed === 0
              ? "Cleanup complete — no stale entries found"
              : `Cleanup complete — ${msg.removed} stale ${msg.removed === 1 ? "entry" : "entries"} removed`,
              C.blue,
            );
            fetchAll();
            setHistoryRefreshKey(k => k + 1);
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
                  msg.status === "success" ? C.green
                  : msg.status === "undone" ? C.blue
                  : C.red,
            );
            fetchForge();
            setForgeRefreshKey(k => k + 1);
            break;
        }
      }, [fetchAll, fetchForge, toast]);

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
        historyRefreshKey, setHistoryRefreshKey,
        forgeActive, forgeProcessed, forgeRefreshKey,
          toast, fetchAll, fetchForge,
          pendingQueue, wsConnected, isMobile,
      };
}
