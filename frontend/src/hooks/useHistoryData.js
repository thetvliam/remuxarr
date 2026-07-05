import { useState, useEffect, useCallback, useRef } from "react";

const PAGE_SIZE = 50;

/* ═══════════════════════════════════════════════════════════════════════════
   useHistoryData
   Manages server-side paginated fetching for one history tab.

   Parameters:
     api          — base URL string
     status       — "all" | "success" | "failed" | "skipped" | "dry_run"
     refreshKey   — { key: number, status: string | null }. `key` increments
                    whenever history may have changed; `status` records
                    WHICH specific status just changed (from a job_completed
                    event), or null if the change could affect any status
                    (scan/cleanup, which can create or remove items across
                    multiple tabs at once).
     search       — debounced search string

   Returns:
     items    — array of history items fetched so far
     total    — total matching count from the server
     loading  — true while a page request is in flight
     hasMore  — true when the server has more pages beyond what's loaded
     loadMore — call to fetch the next page (used by IntersectionObserver)

   Relevance gating:
   Every job completion used to reset and refetch EVERY tab, regardless of
   whether that tab had anything to do with the job that just finished — a
   failed job would still blank and reload the Success and Skipped tabs,
   producing a visible flash on tabs nothing actually changed on. This hook
   now only proceeds with a reset+refetch when refreshKey.status matches
   (or could affect) this hook's own `status`. A genuine tab switch or
   search change still always refetches — only an irrelevant refreshKey
   bump is now a no-op, leaving the currently displayed items untouched.

   Distinguishing "refreshKey changed" from "status/search changed" (which
   must NOT be gated — switching tabs should always show fresh data) is
   done by tracking the previous api/status/search in refs and comparing
   them at the top of the effect: if none of those three changed since the
   last run, whatever triggered this run must have been refreshKey itself.

   Race condition handling:
   A generationRef tracks which effect invocation is current. When any of
   the dependencies change (status, refreshKey, search), the effect's
   generation increments. Any async operation that resolves after the
   generation has changed is silently dropped — including the finally block
   that clears loadingRef. Without this, the old finally block would reset
   loadingRef for the newer fetch, causing stale results or missing updates.
   This is the fix for the skipped tab not updating after a scan completes.
═══════════════════════════════════════════════════════════════════════════ */

// True if a change tagged with eventStatus should cause a tab showing
// `tab` to refresh. Mirrors history.py's own status filter — the Failed
// tab's backend query also matches "cancelled", so a cancelled job (e.g.
// from Abort) must still refresh that tab even though the literal status
// string differs.
function eventAffectsTab(eventStatus, tab) {
  if (eventStatus == null) return true;          // scan/cleanup — always relevant
  if (tab === "all") return true;                 // "all" shows every status
  if (eventStatus === tab) return true;
  if (tab === "failed" && eventStatus === "cancelled") return true;
  return false;
}

export function useHistoryData(api, status, refreshKey, search) {
  const [items,   setItems]   = useState([]);
  const [total,   setTotal]   = useState(0);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);

  const offsetRef     = useRef(0);
  const loadingRef    = useRef(false);
  const abortRef      = useRef(null);
  const doFetchRef    = useRef(null);
  const generationRef = useRef(0);   // increments on every effect run

  // Track previous values so the effect can tell whether IT was refreshKey
  // specifically that changed, as opposed to a tab switch or search edit
  // (both of which must always refetch regardless of refreshKey.status).
  const prevApiRef    = useRef(api);
  const prevStatusRef = useRef(status);
  const prevSearchRef = useRef(search);

  useEffect(() => {
    const apiChanged    = prevApiRef.current    !== api;
    const statusChanged = prevStatusRef.current !== status;
    const searchChanged = prevSearchRef.current !== search;
    // If none of api/status/search changed, this effect run must have been
    // triggered by refreshKey itself — the only remaining dependency.
    const onlyRefreshKeyChanged = !apiChanged && !statusChanged && !searchChanged;

    prevApiRef.current    = api;
    prevStatusRef.current = status;
    prevSearchRef.current = search;

    if (onlyRefreshKeyChanged && !eventAffectsTab(refreshKey.status, status)) {
      // Irrelevant change for this tab — leave items exactly as they are.
      // No reset, no fetch, no loading flash.
      return;
    }

    // Cancel any in-flight request from a previous generation
    if (abortRef.current) abortRef.current.abort();
    loadingRef.current = false;

    // Advance generation — any still-running async ops from the previous
    // generation will see the mismatch and discard their results.
    const myGeneration = ++generationRef.current;

    offsetRef.current = 0;
    setItems([]);
    setTotal(0);
    setHasMore(false);

    const doFetch = async (fetchOffset, append) => {
      if (loadingRef.current) return;
      if (generationRef.current !== myGeneration) return; // already superseded

      const ctrl = new AbortController();
      abortRef.current  = ctrl;
      loadingRef.current = true;
      setLoading(true);

      try {
        const params = new URLSearchParams({ limit: PAGE_SIZE, offset: fetchOffset });
        if (status && status !== "all") params.set("status", status);
        if (search.trim())              params.set("search", search.trim());

        const r = await fetch(`${api}/api/history/?${params}`, { signal: ctrl.signal });

        // Check generation again after every await — the effect may have
        // re-run while the network request was in flight.
        if (generationRef.current !== myGeneration) return;
        if (!r.ok) return;

        const data     = await r.json();

        if (generationRef.current !== myGeneration) return;

        const newItems = data.items || [];
        const newTotal = data.total  ?? 0;
        const next     = fetchOffset + newItems.length;

        setTotal(newTotal);
        setHasMore(next < newTotal);
        offsetRef.current = next;
        setItems(append ? (prev => [...prev, ...newItems]) : newItems);

      } catch (err) {
        if (err.name !== "AbortError") {
          console.error("useHistoryData fetch error:", err);
        }
      } finally {
        // Only clear loading state if we are still the current generation.
        // If we clear it for a stale generation, we would reset the loading
        // flag that the newer generation is currently using — causing the
        // refresh triggered by historyRefreshKey to silently drop its results.
        if (generationRef.current === myGeneration) {
          loadingRef.current = false;
          setLoading(false);
        }
      }
    };

    doFetchRef.current = doFetch;
    doFetch(0, false);

    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, [api, status, refreshKey, search]); // eslint-disable-line react-hooks/exhaustive-deps

  // Stable callback — reads from refs so it never goes stale
  const loadMore = useCallback(() => {
    if (!loadingRef.current && doFetchRef.current) {
      doFetchRef.current(offsetRef.current, true);
    }
  }, []);

  return { items, total, loading, hasMore, loadMore };
}
