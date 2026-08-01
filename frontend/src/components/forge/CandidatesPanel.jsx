import { useState, useEffect, useRef } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { fmtSize, fmtDur } from "../../utils";
import { LED } from "../atoms/LED";
import { EmptyState } from "../atoms/EmptyState";
import { PanelHeader } from "../layout/PanelHeader";
import { usePaginatedFetch } from "../../hooks/usePaginatedFetch";

/* ═══════════════════════════════════════════════════════════════════════════
 * CANDIDATE ROW
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */
const CandidateRow = ({ candidate: c, onAdd }) => {
    const { palette, type, space, radius, size, surface } = useTheme();
    const [hover, setHover] = useState(false);
    const lang = c.aac_track?.language?.toUpperCase() || "UND";

    return (
        <div
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
            display: "flex",
            alignItems: "center",
            gap: space.md,
            padding: `${space.md}px ${space.xl}px`,
            background: hover ? surface.rowHoverBg : "transparent",
            borderBottom: `1px solid ${palette.border}`,
            transition: "background 0.1s",
        }}
        >
        <LED color={palette.green} size={size.ledSizeSm} />

        <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
            color: palette.text, fontSize: type.size.base, fontWeight: type.weight.medium,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
            marginBottom: space.hair,
        }}>
        {c.filename}
        </div>
        <div style={{ display: "flex", gap: space.sm, alignItems: "center" }}>
        <span style={{
            padding: `${space.hair}px ${space.xs}px`,
            background: alpha(palette.amber, ALPHA.low),
            border: `1px solid ${alpha(palette.amber, ALPHA.strong)}`,
            borderRadius: radius.sm,
            color: palette.amber, fontSize: type.size.xs,
            fontFamily: type.family, letterSpacing: type.tracking.wide,
        }}>
        {lang} · AAC 5.1
        </span>
        <span style={{ color: palette.dim, fontSize: type.size.sm }}>{fmtSize(c.size)}</span>
        <span style={{ color: palette.dim, fontSize: type.size.sm }}>{fmtDur(c.duration)}</span>
        <span style={{ color: palette.dim, fontSize: type.size.sm }}>{(c.container || "").toUpperCase()}</span>
        </div>
        </div>

        <button
        onClick={() => onAdd(c.id)}
        style={{
            padding: `${space.xxs}px ${space.lg}px`, flexShrink: 0,
            background: hover ? alpha(palette.amber, ALPHA.medium) : "transparent",
            border: `1px solid ${hover ? palette.amber : palette.border}`,
            borderRadius: radius.sm,
            color: hover ? palette.amber : palette.dim,
            fontSize: type.size.xs, fontFamily: type.family,
            fontWeight: type.weight.bold, letterSpacing: type.tracking.wide,
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
    const { palette, type, space, radius } = useTheme();
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
            padding: `${space.sm}px ${space.xl}px`,
            background: palette.card,
            borderBottom: `1px solid ${palette.border}`,
            flexShrink: 0,
        }}>
        <input
        value={search}
        onChange={e => setSearch(e.target.value)}
        placeholder="Search all candidates by filename…"
        style={{
            width: "100%",
            padding: `${space.xs}px ${space.md}px`,
            background: palette.bg,
            border: `1px solid ${search ? alpha(palette.amber, ALPHA.half) : palette.border}`,
            borderRadius: radius.sm,
            color: palette.text,
            fontFamily: type.family,
            fontSize: type.size.md,
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
                <div ref={sentinelRef} style={{ padding: `${space.sm}px ${space.xl}px` }}>
                {loading && (
                    <span style={{ color: palette.dim, fontSize: type.size.sm }}>Loading…</span>
                )}
                </div>
            )}

            {/* End-of-list indicator */}
            {!hasMore && items.length > 0 && (
                <div style={{ padding: `${space.sm}px ${space.xl}px` }}>
                <span style={{ color: palette.dim, fontSize: type.size.sm }}>
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
            <div style={{ padding: `${space.xl}px ${space.xl}px` }}>
            <span style={{ color: palette.dim, fontSize: type.size.sm }}>Loading…</span>
            </div>
        )}
        </div>
        </div>
    );
};
