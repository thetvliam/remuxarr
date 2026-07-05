import { useState, useEffect, useCallback, useRef } from "react";

// Larger than History/Forge's PAGE_SIZE (50) deliberately — the primary
// workflow here is "search a show name, select all matching episodes."
// A long-running show can have 200+ episodes; a bigger page means the
// common case fits in a single fetch, so "select all currently loaded"
// behaves the same as "select every matching result" without needing
// separate server-side select-all-by-search logic.
const PAGE_SIZE = 100;

/* ═══════════════════════════════════════════════════════════════════════════
 *  useAudioLanguageReviewData
 *  Self-fetching paginated list for the Audio Language Review section.
 *  Same generation-counter race protection as useHistoryData/useCandidatesData
 *  — see those hooks for the full rationale.
 ═ *══════════════════════════════════════════════════════════════════════════ */
export function useAudioLanguageReviewData(api, refreshKey, search) {
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

                const r = await fetch(`${api}/api/audio-language-review/?${params}`, { signal: ctrl.signal });

                if (generationRef.current !== myGeneration) return;
                if (!r.ok) return;

                const data = await r.json();

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
                    console.error("useAudioLanguageReviewData fetch error:", err);
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
