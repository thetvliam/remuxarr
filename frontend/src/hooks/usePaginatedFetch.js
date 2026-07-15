import { useState, useEffect, useCallback, useRef } from "react";

/* ═══════════════════════════════════════════════════════════════════════════
   usePaginatedFetch
   Shared implementation for server-side paginated fetching, consolidating
   what were three separate, near-identical hooks — useAudioLanguageReviewData,
   useSubtitleLanguageReviewData, and useCandidatesData — that differed only
   in which endpoint they fetched from and their page size.

   NOTE: useHistoryData is a fourth, related hook that was deliberately left
   OUT of this consolidation — it has real, substantial extra behavior (a
   "relevance gating" mechanism that skips a redundant reset+refetch when a
   refreshKey change couldn't possibly affect that specific tab's contents,
   built specifically to fix a documented real bug: the skipped tab not
   updating after a scan completes). That's deliberate, bug-fix-driven
   complexity, not accidental duplication — forcing it into this shared,
   simpler hook would mean either bloating this hook with a parameter only
   one caller needs, or risking that carefully-built logic for no real gain.

   Parameters:
     api          — base URL string
     endpoint     — path appended to api, e.g. "/api/forge/candidates/"
                    (must accept ?limit=&offset=&search= query params and
                    return { items, total })
     refreshKey   — value that increments/changes when the list may have
                    changed
     search       — debounced search string
     pageSize     — items requested per page (default 50)

   Returns:
     items    — array of items fetched so far
     total    — total matching count from the server
     loading  — true while a page request is in flight
     hasMore  — true when the server has more pages beyond what's loaded
     loadMore — call to fetch the next page (used by IntersectionObserver)

   Race condition handling:
   A generationRef tracks which effect invocation is current. When any of
   the dependencies change (api, endpoint, refreshKey, search), the effect's
   generation increments. Any async operation that resolves after the
   generation has changed is silently dropped — including the finally block
   that clears loadingRef. Without this, an old finally block could reset
   loadingRef for a newer fetch, causing stale results or missing updates.
═══════════════════════════════════════════════════════════════════════════ */
export function usePaginatedFetch(api, endpoint, refreshKey, search, pageSize = 50) {
  const [items,   setItems]   = useState([]);
  const [total,   setTotal]   = useState(0);
  const [loading, setLoading] = useState(false);
  const [hasMore, setHasMore] = useState(false);

  const offsetRef     = useRef(0);
  const loadingRef    = useRef(false);
  const abortRef      = useRef(null);
  const doFetchRef    = useRef(null);
  const generationRef = useRef(0);

  useEffect(() => {
    if (abortRef.current) abortRef.current.abort();
    loadingRef.current = false;

    const myGeneration = ++generationRef.current;

    offsetRef.current = 0;
    setItems([]);
    setTotal(0);
    setHasMore(false);

    const doFetch = async (fetchOffset, append) => {
      if (loadingRef.current) return;
      if (generationRef.current !== myGeneration) return;

      const ctrl = new AbortController();
      abortRef.current   = ctrl;
      loadingRef.current = true;
      setLoading(true);

      try {
        const params = new URLSearchParams({ limit: pageSize, offset: fetchOffset });
        if (search.trim()) params.set("search", search.trim());

        const r = await fetch(`${api}${endpoint}?${params}`, { signal: ctrl.signal });

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
          console.error(`usePaginatedFetch (${endpoint}) fetch error:`, err);
        }
      } finally {
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
  }, [api, endpoint, refreshKey, search]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadMore = useCallback(() => {
    if (!loadingRef.current && doFetchRef.current) {
      doFetchRef.current(offsetRef.current, true);
    }
  }, []);

  return { items, total, loading, hasMore, loadMore };
}
