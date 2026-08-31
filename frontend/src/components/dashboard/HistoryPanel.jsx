import { useState, useEffect, useRef } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { fmtRel, fmtCount, formatBytesSaved } from "../../utils";
import { LED } from "../atoms/LED";
import { EmptyState } from "../atoms/EmptyState";
import { PanelHeader } from "../layout/PanelHeader";
import { useHistoryData } from "../../hooks/useHistoryData";

/* ═══════════════════════════════════════════════════════════════════════════
 * HISTORY ROW
 ═══════════════════════════════════════════════════════════════════════════ */
const HistoryRow = ({ item, onSelect }) => {
  const { palette, type, space, radius, size, surface, statusColor } = useTheme();
  const [hover, setHover] = useState(false);
  const f      = item.file || {};
  const ok     = item.status === "success";
  const dryRun = item.status === "dry_run";
  const bs     = formatBytesSaved(item.bytes_saved, item.bytes_saved_pct);

  return (
    <button
    onClick={() => onSelect(item)}
    onMouseEnter={() => setHover(true)}
    onMouseLeave={() => setHover(false)}
    style={{
      display: "block",
      width: "100%",
      textAlign: "left",
      padding: `${space.md}px ${space.xl}px`,
      background: hover ? surface.rowHoverBg : "transparent",
      border: "none",
      borderBottom: `1px solid ${palette.border}`,
      cursor: "pointer",
      fontFamily: type.family,
    }}
    >
    <div style={{ display: "flex", alignItems: "center", gap: space.sm, marginBottom: space.xxs }}>
    <LED color={statusColor[item.status] || palette.dim} size={size.ledSizeSm} />
    <span style={{
      color: palette.text,
      fontSize: type.size.base,
      fontWeight: type.weight.medium,
      flex: 1,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    }}>
    {f.filename || "—"}
    </span>
    {dryRun && (
      <span style={{
        padding: `${space.hair}px ${space.xs}px`,
        background: alpha(palette.violet, ALPHA.low),
                border: `1px solid ${alpha(palette.violet, ALPHA.strong)}`,
                borderRadius: radius.sm,
                color: palette.violet,
                fontSize: type.size.xs,
                letterSpacing: type.tracking.wide,
                flexShrink: 0,
      }}>
      PREVIEW
      </span>
    )}
    <span style={{ color: palette.dim, fontSize: type.size.xs, flexShrink: 0 }}>
    {fmtRel(item.completed_at)}
    </span>
    </div>

    <div style={{ paddingLeft: space.xl }}>
    {dryRun && (
      <span style={{
        color: palette.muted, fontSize: type.size.sm,
        overflow: "hidden", textOverflow: "ellipsis",
        whiteSpace: "nowrap", display: "block",
      }}>
      Would: {item.reason || "—"}
      </span>
    )}
    {ok && bs ? (
      bs.isPositive ? (
        <span style={{ color: palette.green, fontSize: type.size.sm }}>
        −{bs.sizeText} ({bs.pctDisplay}%)
        </span>
      ) : bs.isNegative ? (
        <span style={{ color: palette.dim, fontSize: type.size.sm }}>+{bs.sizeText} overhead</span>
      ) : (
        <span style={{ color: palette.muted, fontSize: type.size.sm }}>no size change</span>
      )
    ) : ok ? (
      <span style={{ color: palette.muted, fontSize: type.size.sm }}>processed</span>
    ) : null}
    {item.status === "skipped" && (
      <span style={{
        color: palette.dim, fontSize: type.size.sm,
        overflow: "hidden", textOverflow: "ellipsis",
        whiteSpace: "nowrap", display: "block",
      }}>
      {item.reason || "No changes needed"}
      </span>
    )}
    {!ok && !dryRun && item.status !== "skipped" && (
      <span style={{
        color: palette.red, fontSize: type.size.sm,
        overflow: "hidden", textOverflow: "ellipsis",
        whiteSpace: "nowrap", display: "block",
      }}>
      {(item.error_message || "failed").slice(0, 72)}
      </span>
    )}
    </div>
    </button>
  );
};

/* ═══════════════════════════════════════════════════════════════════════════
 * HISTORY PANEL
 * Self-fetching: receives api + historyRefreshKey instead of a pre-loaded
 * items array.  useHistoryData handles pagination; IntersectionObserver
 * triggers loadMore when the scroll sentinel comes into view.
 ═══════════════════════════════════════════════════════════════════════════ */
export const HistoryPanel = ({ api, historyRefreshKey, onSelect, onRetryAll, onClearDryRun }) => {
  const { palette, type, space, radius } = useTheme();
  const [tab,            setTab]            = useState("success");
  const [search,         setSearch]         = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [counts,         setCounts]         = useState({ success: 0, failed: 0, skipped: 0, dry_run: 0 });

  const scrollRef   = useRef(null);
  const sentinelRef = useRef(null);

  // Debounce: fire the actual fetch 300ms after the user stops typing
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Clear search when switching tabs so previous results don't linger
  const switchTab = (newTab) => {
    setTab(newTab);
    setSearch("");
    setDebouncedSearch("");
  };

  // Summary counts for tab badges (unaffected by search)
  useEffect(() => {
    fetch(`${api}/api/history/summary`)
    .then(r => r.json())
    .then(d => setCounts({
      success: d.success  || 0,
      failed:  d.failed   || 0,   // already includes cancelled
      skipped: d.skipped  || 0,
      dry_run: d.dry_run  || 0,
    }))
    .catch(() => {});
  }, [api, historyRefreshKey]);

  // Paginated items for the active tab + search
  const { items, total, loading, hasMore, loadMore } = useHistoryData(
    api, tab, historyRefreshKey, debouncedSearch,
  );

  // IntersectionObserver — fires loadMore when sentinel enters the scroll area
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
  }, [hasMore, loading, loadMore]); // re-setup after each load so we catch "still in view"

  // ── Tab header ────────────────────────────────────────────────────────────
  const tabs = (
    <div style={{ display: "flex", alignItems: "center" }}>
    {[
      ["success", palette.green],
      ["skipped", palette.muted],
      ["failed",  palette.red],
      ["dry_run", palette.violet],
    ].map(([key, color]) => {
      const n       = counts[key] || 0;
      const label   = key === "dry_run" ? "DRY RUN" : key.toUpperCase();
      const tooltip = n >= 1000 ? n.toLocaleString() + " items" : undefined;
      return (
        <button
        key={key}
        onClick={() => switchTab(key)}
        title={tooltip}
        style={{
          padding: `${space.hair}px ${space.md}px`,
          background: tab === key ? alpha(color, ALPHA.low) : "transparent",
              // No borderRadius here, deliberately. These four buttons are a
              // single segmented control: each drops its right border so the
              // neighbouring one's left border serves both. Rounding a segment
              // would round the edges it shares, leaving gaps down the middle of
              // the strip on any theme with a real radius. Only the outer two
              // corners of the group should curve, which needs a clipping
              // wrapper around just the segments — the flex container here also
              // holds the divider and the RETRY ALL button, so it cannot take
              // that role without clipping those too.
              border: `1px solid ${tab === key ? color : palette.border}`,
              borderRight: "none",
              color: tab === key ? color : palette.dim,
              fontSize: type.size.xs,
              fontFamily: type.family,
              letterSpacing: type.tracking.wide,
              cursor: "pointer",
        }}
        >
        {label}
        {n > 0 && (
          <span style={{ marginLeft: space.xs, color }}>{fmtCount(n)}</span>
        )}
        </button>
      );
    })}
    {/* Vertical rule between the tab strip and RETRY ALL. alignSelf is
      * required: the flex parent sets alignItems:"center", which overrides
      * the default stretch, so with only a width this element had zero
      * height and never painted at all. */}
      <div style={{ width: 1, alignSelf: "stretch", background: palette.border }} />

      {tab === "failed" && counts.failed > 0 && !debouncedSearch && (
        <button
        onClick={onRetryAll}
        title="Re-probe and re-queue every failed and cancelled item"
        style={{
          marginLeft: space.sm,
          padding: `${space.hair}px ${space.md}px`,
          background: "transparent",
          border: `1px solid ${palette.amber}`,
          borderRadius: radius.sm,
          color: palette.amber,
          fontSize: type.size.xs,
          fontFamily: type.family,
          letterSpacing: type.tracking.wide,
          cursor: "pointer",
        }}
        >
        ↺ RETRY ALL
        </button>
      )}

      {tab === "dry_run" && counts.dry_run > 0 && !debouncedSearch && (
        <button
        onClick={onClearDryRun}
        title="Discard every dry-run preview — none of these files will be processed"
        style={{
          marginLeft: space.sm,
          padding: `${space.hair}px ${space.md}px`,
          background: "transparent",
          border: `1px solid ${palette.violet}`,
          borderRadius: radius.sm,
          color: palette.violet,
          fontSize: type.size.xs,
          fontFamily: type.family,
          letterSpacing: type.tracking.wide,
          cursor: "pointer",
        }}
        >
        × CLEAR ALL
        </button>
      )}
      </div>
  );

  // ── Panel count badge — unfiltered total, or search result count ──────────
  const headerCount = debouncedSearch
  ? total   // show search result count (from paginated response)
  : counts[tab] || 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
    <PanelHeader label="HISTORY" count={headerCount} right={tabs} />

    {/* Search — always visible so the user can search immediately */}
    <div style={{
      padding: `${space.xs}px ${space.lg}px`,
      borderBottom: `1px solid ${palette.border}`,
      flexShrink: 0,
    }}>
    <input
    value={search}
    onChange={e => setSearch(e.target.value)}
    placeholder="Search all history by filename…"
    style={{
      width: "100%",
      padding: `${space.xxs}px ${space.sm}px`,
      background: palette.bg,
      border: `1px solid ${search ? alpha(palette.amber, ALPHA.half) : palette.border}`,
          borderRadius: radius.sm,
          color: palette.text,
          fontSize: type.size.md,
          fontFamily: type.family,
    }}
    />
    </div>

    {/* Item list */}
    <div ref={scrollRef} style={{ flex: 1, overflowY: "auto" }}>
    {items.length === 0 && !loading ? (
      debouncedSearch ? (
        <EmptyState msg={`No ${tab} items match "${debouncedSearch}"`} />
      ) : (
        <EmptyState msg={
          tab === "dry_run" ? "No dry-run previews"
          : tab === "skipped" ? "No skipped files — run a scan to populate this tab"
          : `No ${tab} items`
        } />
      )
    ) : (
      <>
      {items.map(item => (
        <HistoryRow key={item.id} item={item} onSelect={onSelect} />
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
          : `${items.length.toLocaleString()} item${items.length === 1 ? "" : "s"}`
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
