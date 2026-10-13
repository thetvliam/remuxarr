import { useState, useEffect, useRef } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { fmtRel, fmtCount, formatBytesSaved } from "../../utils";
import { LED } from "../atoms/LED";
import { EmptyState } from "../atoms/EmptyState";
import { PanelHeader } from "../layout/PanelHeader";
import { SegmentedControl } from "../atoms/SegmentedControl";
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
      borderTop: "none",
      borderRight: "none",
      borderBottom: `1px solid ${palette.border}`,
      borderLeft: "none",
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
  const [counts,         setCounts]         = useState({ success: 0, failed: 0, failed_only: 0, skipped: 0, dry_run: 0 });

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
    /* This effect re-runs on every historyRefreshKey bump and job completions
     * bump it, so two of these can be in flight at once. `stale` is what stops
     * the older one landing last and leaving badges that disagree with the
     * list under them — and stops a write after unmount.
     *
     * A flag rather than an AbortController, unlike the two hooks below,
     * which abort because they cancel large paginated reads that the user is
     * actively scrolling. This is four integers. The flag also closes a
     * window abort cannot: once json() has resolved, aborting no longer
     * prevents the setState that follows it.
     *
     * The status check is the other half. fetch does not reject on an HTTP
     * error, so an error body reached the reads below, every `|| 0` found
     * undefined on it, and all four badges dropped to zero over a list that
     * still had rows. Returning early leaves the counts alone, which is what
     * the catch already did for a network failure — the two paths disagreed
     * only because this one was never checked. */
    let stale = false;
    (async () => {
      try {
        const r = await fetch(`${api}/api/history/summary`);
        if (!r.ok) return;
        const d = await r.json();
        if (stale) return;
        setCounts({
          success: d.success  || 0,
          failed:  d.failed   || 0,   // already includes cancelled
          /* Failed rows alone, which is all Retry All acts on — it leaves
           * cancelled rows where they are (queue.retry_all_failed). Only the
           * button reads this. The badge and the header keep `failed`,
           * because the tab lists cancelled rows too and its count has to
           * agree with them. */
          failed_only: d.failed_only || 0,
          skipped: d.skipped  || 0,
          dry_run: d.dry_run  || 0,
        });
      } catch {
        // Network failure: keep whatever the badges already show.
      }
    })();
    return () => { stale = true; };
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
  /* The tabs take a row of their own under the title, across the panel, and
   * the tab's action (RETRY ALL, CLEAR ALL) sits on the title row. They all
   * shared one row before, which needs 517px to 679px depending on the theme
   * and the counts, measured in Chrome. On a phone the panel is the screen's
   * width and on a desktop half the window's, so below that the tabs were
   * squeezed until their labels broke, and DRY RUN and the action ran past
   * the panel's edge, out of reach. Always two rows, rather than one when it
   * fits, so the header does not jump when the action appears or a count
   * grows during processing. */
  const tabs = (
    <SegmentedControl
    label="History"
    fill
    value={tab}
    onChange={switchTab}
    options={[
      ["success", palette.green],
      ["skipped", palette.muted],
      ["failed",  palette.red],
      ["dry_run", palette.violet],
    ].map(([key, color]) => {
      const n = counts[key] || 0;
      return {
        value: key,
        label: key === "dry_run" ? "DRY RUN" : key.toUpperCase(),
        color,
        badge: n > 0 ? fmtCount(n) : null,
        title: n >= 1000 ? n.toLocaleString() + " items" : undefined,
      };
    })}
    />
  );

  /* Gated on failed_only rather than failed: over a tab holding only
   * cancelled rows, the button would do nothing. */
  const actions = (
    <>
    {tab === "failed" && counts.failed_only > 0 && !debouncedSearch && (
      <button
      onClick={onRetryAll}
      title="Re-probe and re-queue every failed item. Cancelled items are left alone."
      style={{
        padding: `${space.hair}px ${space.md}px`,
        background: "transparent",
        border: `1px solid ${palette.amber}`,
        borderRadius: radius.sm,
        color: palette.amber,
        fontSize: type.size.xs,
        fontFamily: type.family,
        letterSpacing: type.tracking.wide,
        whiteSpace: "nowrap",
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
        padding: `${space.hair}px ${space.md}px`,
        background: "transparent",
        border: `1px solid ${palette.violet}`,
        borderRadius: radius.sm,
        color: palette.violet,
        fontSize: type.size.xs,
        fontFamily: type.family,
        letterSpacing: type.tracking.wide,
        whiteSpace: "nowrap",
        cursor: "pointer",
      }}
      >
      × CLEAR ALL
      </button>
    )}
    </>
  );

  // ── Panel count badge — unfiltered total, or search result count ──────────
  const headerCount = debouncedSearch
  ? total   // show search result count (from paginated response)
  : counts[tab] || 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
    <PanelHeader label="HISTORY" count={headerCount} right={actions} below={tabs} />

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
