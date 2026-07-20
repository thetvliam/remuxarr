import { useState, useEffect, useRef } from "react";
import { C } from "../../constants";
import { fmtSize, fmtDur } from "../../utils";
import { LED } from "../atoms/LED";
import { EmptyState } from "../atoms/EmptyState";
import { PanelHeader } from "../layout/PanelHeader";
import { usePaginatedFetch } from "../../hooks/usePaginatedFetch";

/* ═══════════════════════════════════════════════════════════════════════════
 * CANDIDATE ROW
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */
const CandidateRow = ({ candidate: c, onAdd }) => {
    const [hover, setHover] = useState(false);
    const lang = c.aac_track?.language?.toUpperCase() || "UND";

    return (
        <div
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "9px 14px",
            background: hover ? "#ffffff07" : "transparent",
            borderBottom: `1px solid ${C.border}`,
            transition: "background 0.1s",
        }}
        >
        <LED color={C.green} size={6} />

        <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
            color: C.text, fontSize: 12, fontWeight: 500,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            marginBottom: 2,
        }}>
        {c.filename}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <span style={{
            padding: "1px 6px",
            background: C.amber + "18",
            border: `1px solid ${C.amber}44`,
            color: C.amber, fontSize: 9,
            fontFamily: "inherit", letterSpacing: "0.1em",
        }}>
        {lang} · AAC 5.1
        </span>
        <span style={{ color: C.dim, fontSize: 10 }}>{fmtSize(c.size)}</span>
        <span style={{ color: C.dim, fontSize: 10 }}>{fmtDur(c.duration)}</span>
        <span style={{ color: C.dim, fontSize: 10 }}>{(c.container || "").toUpperCase()}</span>
        </div>
        </div>

        <button
        onClick={() => onAdd(c.id)}
        style={{
            padding: "4px 12px", flexShrink: 0,
            background: hover ? C.amber + "22" : "transparent",
            border: `1px solid ${hover ? C.amber : C.border}`,
            color: hover ? C.amber : C.dim,
            fontSize: 9, fontFamily: "inherit",
            fontWeight: 700, letterSpacing: "0.1em",
            cursor: "pointer", transition: "all 0.15s", whiteSpace: "nowrap",
        }}
        >
        + ADD AC3
        </button>
        </div>
    );
};

/* ═══════════════════════════════════════════════════════════════════════════
 * CANDIDATES PANEL
 * Self-fetching: receives api + forgeRefreshKey instead of a pre-loaded
 * candidates array.  usePaginatedFetch handles pagination; the same
 * IntersectionObserver + generation-counter pattern used in HistoryPanel
 * ensures refreshKey changes always produce a clean, up-to-date list.
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */
export const CandidatesPanel = ({ api, forgeRefreshKey, onAdd }) => {
    const [search,          setSearch]          = useState("");
    const [debouncedSearch, setDebouncedSearch] = useState("");

    const scrollRef   = useRef(null);
    const sentinelRef = useRef(null);

    // Debounce: fire the actual fetch 300 ms after the user stops typing
    useEffect(() => {
        const t = setTimeout(() => setDebouncedSearch(search), 300);
        return () => clearTimeout(t);
    }, [search]);

    const { items, total, loading, hasMore, loadMore } = usePaginatedFetch(
        api, "/api/forge/candidates/", forgeRefreshKey, debouncedSearch,
    );

    // IntersectionObserver — trigger loadMore when sentinel enters scroll area
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

    return (
        <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
        {/* Search bar */}
        <div style={{
            padding: "8px 14px",
            background: C.card,
            borderBottom: `1px solid ${C.border}`,
            flexShrink: 0,
        }}>
        <input
        value={search}
        onChange={e => setSearch(e.target.value)}
        placeholder="Search all candidates by filename…"
        style={{
            width: "100%",
            padding: "5px 10px",
            background: C.bg,
            border: `1px solid ${search ? C.amber + "88" : C.border}`,
            color: C.text,
            fontFamily: "inherit",
            fontSize: 11,
            outline: "none",
        }}
        />
        </div>

        <PanelHeader
        label="AAC 5.1 CANDIDATES"
        count={total}
        />

        <div ref={scrollRef} style={{ flex: 1, overflowY: "auto" }}>
        {items.length === 0 && !loading ? (
            <EmptyState msg={
                debouncedSearch
                ? `No candidates match "${debouncedSearch}"`
                : "No files with AAC 5.1 audio found — run a library scan first"
            } />
        ) : (
            <>
            {items.map(c => (
                <CandidateRow key={c.id} candidate={c} onAdd={onAdd} />
            ))}

            {/* Infinite scroll sentinel */}
            {hasMore && (
                <div ref={sentinelRef} style={{ padding: "8px 14px" }}>
                {loading && (
                    <span style={{ color: C.dim, fontSize: 10 }}>Loading…</span>
                )}
                </div>
            )}

            {/* End-of-list indicator */}
            {!hasMore && items.length > 0 && (
                <div style={{ padding: "8px 14px" }}>
                <span style={{ color: C.dim, fontSize: 10 }}>
                {debouncedSearch
                    ? `${total.toLocaleString()} result${total === 1 ? "" : "s"}`
                    : `${items.length.toLocaleString()} candidate${items.length === 1 ? "" : "s"}`
                }
                </span>
                </div>
            )}
            </>
        )}

        {/* Loading spinner for first-page load */}
        {items.length === 0 && loading && (
            <div style={{ padding: "16px 14px" }}>
            <span style={{ color: C.dim, fontSize: 10 }}>Loading…</span>
            </div>
        )}
        </div>
        </div>
    );
};
