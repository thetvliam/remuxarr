import { useState, useEffect, useRef } from "react";
import { C } from "../../constants";
import { Btn } from "../atoms/Btn";
import { EmptyState } from "../atoms/EmptyState";
import { usePaginatedFetch } from "../../hooks/usePaginatedFetch";

/* ═══════════════════════════════════════════════════════════════════════════
 * AUDIO LANGUAGE REVIEW SECTION
 * Self-contained: search, multi-select, and two bulk actions. Distinct
 * from the manual-review list above it — files here are already fully
 * processed and playable; this is purely an optional correction workflow.
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
export const AudioLanguageReviewSection = ({ api, onRefresh, setHistoryRefreshKey }) => {
    const [search,          setSearch]          = useState("");
    const [debouncedSearch, setDebouncedSearch] = useState("");
    const [selected,        setSelected]        = useState(new Set());
    const [targetLang,      setTargetLang]      = useState("eng");
    const [refreshKey,      setRefreshKey]      = useState(0);
    const [busy,            setBusy]            = useState(false);

    const scrollRef   = useRef(null);
    const sentinelRef = useRef(null);

    useEffect(() => {
        const t = setTimeout(() => setDebouncedSearch(search), 300);
        return () => clearTimeout(t);
    }, [search]);

    // 100 (vs the shared hook's default of 50) deliberately — the primary
    // workflow here is "search a show name, select all matching episodes."
    // A long-running show can have 200+ episodes; a bigger page means the
    // common case fits in a single fetch, so "select all currently loaded"
    // behaves the same as "select every matching result" without needing
    // separate server-side select-all-by-search logic.
    const { items, total, loading, hasMore, loadMore } = usePaginatedFetch(
        api, "/api/audio-language-review/", refreshKey, debouncedSearch, 100,
    );

    // Clear selection whenever the underlying list changes shape (new
    // search, or a refresh after an action) — stale selected IDs pointing
    // at items no longer shown would be confusing to act on.
    useEffect(() => {
        setSelected(new Set());
    }, [debouncedSearch, refreshKey]);

    useEffect(() => {
        const sentinel = sentinelRef.current;
        const scroll   = scrollRef.current;
        if (!sentinel || !scroll || !hasMore) return;
        const observer = new IntersectionObserver(
            ([entry]) => { if (entry.isIntersecting) loadMore(); },
                                                  { root: scroll, threshold: 0 },
        );
        observer.observe(sentinel);
        return () => observer.disconnect();
    }, [hasMore, loading, loadMore]);

    const toggleOne = (fileId) => {
        setSelected(prev => {
            const next = new Set(prev);
            if (next.has(fileId)) next.delete(fileId);
            else next.add(fileId);
            return next;
        });
    };

    const allLoadedSelected = items.length > 0 && items.every(i => selected.has(i.file_id));
    const toggleAll = () => {
        setSelected(allLoadedSelected ? new Set() : new Set(items.map(i => i.file_id)));
    };

    const applyLanguage = async () => {
        if (selected.size === 0) return;
        const lang = targetLang.trim().toLowerCase();
        if (!lang) return;
        setBusy(true);
        try {
            await fetch(`${api}/api/audio-language-review/apply`, {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ file_ids: Array.from(selected), target_language: lang }),
            });
            setRefreshKey(k => k + 1);
            // This section's own refreshKey above only re-queries ITS OWN
            // flagged-items list — it has no way to tell the main dashboard's
            // queue view, or the History panel's tabs, that anything changed.
            // Applying a correction deletes the file's existing QueueItem and
            // creates a fresh pending one — onRefresh (fetchAll) picks that up
            // for the queue; setHistoryRefreshKey covers History, since the
            // file was most likely sitting in the Success tab already (having
            // been processed once before, just with the wrong language tag).
            onRefresh?.();
            setHistoryRefreshKey?.(prev => ({ key: prev.key + 1, status: null }));
        } finally {
            setBusy(false);
        }
    };

    const ignoreSelected = async () => {
        if (selected.size === 0) return;
        setBusy(true);
        try {
            await fetch(`${api}/api/audio-language-review/ignore`, {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ file_ids: Array.from(selected) }),
            });
            setRefreshKey(k => k + 1);
        } finally {
            setBusy(false);
        }
    };

    return (
        <div style={{ marginTop: 32 }}>
        {/* Section header — visually distinct from the manual-review list above */}
        <div style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginBottom: 8,
            paddingTop: 24,
            borderTop: `1px solid ${C.border}`,
        }}>
        <span style={{ color: C.blue, fontSize: 15 }}>♪</span>
        <span style={{ color: C.dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>
        AUDIO LANGUAGE REVIEW
        </span>
        <span style={{
            padding: "0 6px",
            background: C.blue + "20",
            border: `1px solid ${C.blue}44`,
            color: C.blue,
            fontSize: 9,
        }}>
        {total}
        </span>
        </div>
        <p style={{ color: C.muted, fontSize: 11, margin: "0 0 14px", lineHeight: 1.65 }}>
        Files whose kept audio track has a language tag that doesn't match
        your preferred languages — e.g. an English show mistagged with a
        different language. These files are already fully processed and
        playable; this is optional. Search a show name to select every
        flagged episode at once, then either set the correct language and
        reprocess, or confirm the current tag is already correct (e.g.
        genuinely foreign-language content) to stop it being flagged again.
        </p>

        {/* Search + bulk action bar */}
        <div style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            flexWrap: "wrap",
            marginBottom: 10,
        }}>
        <input
        value={search}
        onChange={e => setSearch(e.target.value)}
        placeholder="Search by filename…"
        style={{
            flex: "1 1 200px",
            padding: "5px 10px",
            background: C.bg,
            border: `1px solid ${search ? C.blue + "88" : C.border}`,
            color: C.text,
            fontFamily: "inherit",
            fontSize: 11,
            outline: "none",
        }}
        />
        <input
        value={targetLang}
        onChange={e => setTargetLang(e.target.value)}
        placeholder="eng"
        title="ISO 639-2/B language code to apply to selected files"
        style={{
            width: 70,
            padding: "5px 8px",
            background: C.bg,
            border: `1px solid ${C.border}`,
            color: C.text,
            fontFamily: "inherit",
            fontSize: 11,
            outline: "none",
            textTransform: "lowercase",
        }}
        />
        <Btn
        label={busy ? "WORKING…" : `SET LANGUAGE (${selected.size})`}
        color={C.green}
        bg={C.green + "18"}
        onClick={applyLanguage}
        disabled={busy || selected.size === 0 || !targetLang.trim()}
        />
        <Btn
        label={busy ? "WORKING…" : `IGNORE (${selected.size})`}
        color={C.dim}
        bg="transparent"
        onClick={ignoreSelected}
        disabled={busy || selected.size === 0}
        />
        </div>

        {items.length === 0 && !loading ? (
            <EmptyState msg={
                debouncedSearch
                ? `No flagged files match "${debouncedSearch}"`
                : "No audio language mismatches found ✓"
            } />
        ) : (
            <div ref={scrollRef} style={{ maxHeight: 420, overflowY: "auto", border: `1px solid ${C.border}` }}>
            {/* Select-all header row */}
            {items.length > 0 && (
                <div style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "6px 12px",
                    background: C.card,
                    borderBottom: `1px solid ${C.border}`,
                    position: "sticky",
                    top: 0,
                }}>
                <input type="checkbox" checked={allLoadedSelected} onChange={toggleAll} />
                <span style={{ color: C.dim, fontSize: 9, letterSpacing: "0.08em" }}>
                SELECT ALL LOADED ({items.length}{total > items.length ? ` of ${total}` : ""})
                </span>
                </div>
            )}

            {items.map(item => (
                <div
                key={item.id}
                onClick={() => toggleOne(item.file_id)}
                style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "8px 12px",
                    borderBottom: `1px solid ${C.border}`,
                    cursor: "pointer",
                    background: selected.has(item.file_id) ? "#ffffff08" : "transparent",
                }}
                >
                <input
                type="checkbox"
                checked={selected.has(item.file_id)}
                onChange={() => toggleOne(item.file_id)}
                onClick={e => e.stopPropagation()}
                />
                <span style={{
                    flex: 1,
                    minWidth: 0,
                    color: C.text,
                    fontSize: 11,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                }}>
                {item.filename}
                </span>
                <span style={{
                    flexShrink: 0,
                    padding: "1px 6px",
                    background: C.yellow + "18",
                    border: `1px solid ${C.yellow}44`,
                    color: C.yellow,
                    fontSize: 9,
                    letterSpacing: "0.1em",
                }}>
                {(item.detected_language || "?").toUpperCase()}
                </span>
                </div>
            ))}

            {hasMore && (
                <div ref={sentinelRef} style={{ padding: "8px 12px" }}>
                {loading && <span style={{ color: C.dim, fontSize: 10 }}>Loading…</span>}
                </div>
            )}
            </div>
        )}
        </div>
    );
};
