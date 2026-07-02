import { useState, useEffect, useCallback, useRef } from "react";

const PAGE_SIZE = 50;

/* ═══════════════════════════════════════════════════════════════════════════
 *  useCandidatesData
 *  Manages server-side paginated fetching of AAC 5.1 forge candidates.
 *
 *  Parameters:
 *    api          — base URL string
 *    refreshKey   — integer that increments when candidates may have changed
 *                   (forge job started/completed, library scan completed)
 *    search       — debounced search string
 *
 *  Returns:
 *    items    — array of candidate items fetched so far
 *    total    — total matching count from the server
 *    loading  — true while a page request is in flight
 *    hasMore  — true when the server has more pages
 *    loadMore — call to fetch the next page (used by IntersectionObserver)
 *
 *  Uses the same generation-counter race-condition fix as useHistoryData:
 *  any async operation that resolves after the generation has changed is
 *  silently discarded, preventing stale results from overwriting fresh ones.
 ═ *══════════════════════════════════════════════════════════════════════════ */
export function useCandidatesData(api, refreshKey, search) {
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
                const params = new URLSearchParams({ limit: PAGE_SIZE, offset: fetchOffset });
                if (search.trim()) params.set("search", search.trim());

                const r = await fetch(`${api}/api/forge/candidates/?${params}`, { signal: ctrl.signal });

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
                    console.error("useCandidatesData fetch error:", err);
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
    }, [api, refreshKey, search]); // eslint-disable-line react-hooks/exhaustive-deps

    const loadMore = useCallback(() => {
        if (!loadingRef.current && doFetchRef.current) {
            doFetchRef.current(offsetRef.current, true);
        }
    }, []);

    return { items, total, loading, hasMore, loadMore };
}
