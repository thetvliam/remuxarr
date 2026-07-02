import { useState, useEffect, useRef } from "react";
import { C, STATUS_COLOR } from "../../constants";
import { fmtRel, fmtCount, formatBytesSaved } from "../../utils";
import { LED } from "../atoms/LED";
import { EmptyState } from "../atoms/EmptyState";
import { PanelHeader } from "../layout/PanelHeader";
import { useHistoryData } from "../../hooks/useHistoryData";

/* ═══════════════════════════════════════════════════════════════════════════
 * HISTORY ROW
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
const HistoryRow = ({ item, onSelect }) => {
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
      padding: "9px 14px",
      background: hover ? "#ffffff07" : "transparent",
      border: "none",
      borderBottom: `1px solid ${C.border}`,
      cursor: "pointer",
      fontFamily: "inherit",
    }}
    >
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
    <LED color={STATUS_COLOR[item.status] || C.dim} size={6} />
    <span style={{
      color: C.text,
      fontSize: 12,
      fontWeight: 500,
      flex: 1,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    }}>
    {f.filename || "—"}
    </span>
    {dryRun && (
      <span style={{
        padding: "1px 6px",
        background: C.violet + "18",
        border: `1px solid ${C.violet}44`,
        color: C.violet,
        fontSize: 9,
        letterSpacing: "0.1em",
        flexShrink: 0,
      }}>
      PREVIEW
      </span>
    )}
    <span style={{ color: C.dim, fontSize: 9, flexShrink: 0 }}>
    {fmtRel(item.completed_at)}
    </span>
    </div>

    <div style={{ paddingLeft: 14 }}>
    {dryRun && (
      <span style={{
        color: C.muted, fontSize: 10,
        overflow: "hidden", textOverflow: "ellipsis",
        whiteSpace: "nowrap", display: "block",
      }}>
      Would: {item.reason || "—"}
      </span>
    )}
    {ok && bs ? (
      bs.isPositive ? (
        <span style={{ color: C.green, fontSize: 10 }}>
        −{bs.sizeText} ({bs.pctDisplay}%)
        </span>
      ) : bs.isNegative ? (
        <span style={{ color: C.dim, fontSize: 10 }}>+{bs.sizeText} overhead</span>
      ) : (
        <span style={{ color: C.muted, fontSize: 10 }}>no size change</span>
      )
    ) : ok ? (
      <span style={{ color: C.muted, fontSize: 10 }}>processed</span>
    ) : null}
    {item.status === "skipped" && (
      <span style={{
        color: C.dim, fontSize: 10,
        overflow: "hidden", textOverflow: "ellipsis",
        whiteSpace: "nowrap", display: "block",
      }}>
      {item.reason || "No changes needed"}
      </span>
    )}
    {!ok && !dryRun && item.status !== "skipped" && (
      <span style={{
        color: C.red, fontSize: 10,
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
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
export const HistoryPanel = ({ api, historyRefreshKey, onSelect, onRetryAll, onClearDryRun }) => {
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
      ["success", C.green],
      ["skipped", C.muted],
      ["failed",  C.red],
      ["dry_run", C.violet],
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
          padding: "2px 10px",
          background: tab === key ? `${color}18` : "transparent",
          border: `1px solid ${tab === key ? color : C.border}`,
          borderRight: "none",
          color: tab === key ? color : C.dim,
          fontSize: 9,
          fontFamily: "inherit",
          letterSpacing: "0.1em",
          cursor: "pointer",
        }}
        >
        {label}
        {n > 0 && (
          <span style={{ marginLeft: 5, color }}>{fmtCount(n)}</span>
        )}
        </button>
      );
    })}
    <div style={{ width: 1, background: C.border }} />

    {tab === "failed" && counts.failed > 0 && !debouncedSearch && (
      <button
      onClick={onRetryAll}
      title="Re-probe and re-queue every failed and cancelled item"
      style={{
        marginLeft: 8,
        padding: "2px 9px",
        background: "transparent",
        border: `1px solid ${C.amber}`,
        color: C.amber,
        fontSize: 9,
        fontFamily: "inherit",
        letterSpacing: "0.1em",
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
        marginLeft: 8,
        padding: "2px 9px",
        background: "transparent",
        border: `1px solid ${C.violet}`,
        color: C.violet,
        fontSize: 9,
        fontFamily: "inherit",
        letterSpacing: "0.1em",
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
      padding: "6px 12px",
      borderBottom: `1px solid ${C.border}`,
      flexShrink: 0,
    }}>
    <input
    value={search}
    onChange={e => setSearch(e.target.value)}
    placeholder="Search all history by filename…"
    style={{
      width: "100%",
      padding: "4px 8px",
      background: C.bg,
      border: `1px solid ${search ? C.amber + "88" : C.border}`,
      color: C.text,
      fontSize: 11,
      fontFamily: "inherit",
      outline: "none",
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
          : `${items.length.toLocaleString()} item${items.length === 1 ? "" : "s"}`
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
