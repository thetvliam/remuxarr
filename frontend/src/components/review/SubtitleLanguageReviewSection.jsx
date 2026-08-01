import { useState, useEffect, useRef } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { Btn } from "../atoms/Btn";
import { EmptyState } from "../atoms/EmptyState";
import { usePaginatedFetch } from "../../hooks/usePaginatedFetch";

/* ═══════════════════════════════════════════════════════════════════════════
 * SUBTITLE LANGUAGE REVIEW SECTION
 * Subtitle counterpart to AudioLanguageReviewSection — identical mechanics
 * (search, multi-select, two bulk actions), mirrored deliberately rather
 * than shared, since the two flag independent things. Every row here
 * originates from an undefined ("und") tag, not a defined-but-wrong one —
 * see fix_undefined_language's "always ask" mode.
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */
export const SubtitleLanguageReviewSection = ({ api, onRefresh, setHistoryRefreshKey }) => {
  const { palette, type, space, radius, legacy } = useTheme();

  // The established subtitle colour in this codebase — the same one the
  // extract_subtitle action badge uses. Read from the palette rather than
  // hardcoded, so it follows the theme like every other colour.
  const SUB_COLOR = palette.cyan;
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

  // Same PAGE_SIZE choice as AudioLanguageReviewSection, for the same
  // reason — see that component for the full rationale.
  const { items, total, loading, hasMore, loadMore } = usePaginatedFetch(
    api, "/api/subtitle-language-review/", refreshKey, debouncedSearch, 100,
  );

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
      await fetch(`${api}/api/subtitle-language-review/apply`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ file_ids: Array.from(selected), target_language: lang }),
      });
      setRefreshKey(k => k + 1);
      // Same reasoning as AudioLanguageReviewSection's own applyLanguage —
      // this section's refreshKey only re-queries its own list; onRefresh
      // and setHistoryRefreshKey cover the queue view and History panel.
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
      await fetch(`${api}/api/subtitle-language-review/ignore`, {
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
    <div style={{ marginTop: space.xxxl }}>
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: space.md,
      marginBottom: space.sm,
      paddingTop: space.huge,
      borderTop: `1px solid ${palette.border}`,
    }}>
    <span style={{ color: SUB_COLOR, fontSize: type.size.xxl }}>▭</span>
    <span style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold }}>
    SUBTITLE LANGUAGE REVIEW
    </span>
    <span style={{
      padding: `0 ${space.xs}px`,
      background: alpha(SUB_COLOR, ALPHA.mild),
          border: `1px solid ${alpha(SUB_COLOR, ALPHA.strong)}`,
          borderRadius: radius.sm,
          color: SUB_COLOR,
          fontSize: type.size.xs,
    }}>
    {total}
    </span>
    </div>
    <p style={{ color: palette.muted, fontSize: type.size.md, margin: `0 0 ${space.xl}px`, lineHeight: type.leading.relaxed }}>
    Files whose kept subtitle track has an undefined language tag,
    flagged because Fix Undefined Language Tags is set to Always Ask.
    These files are already fully processed and playable; this is
    optional. Search a show name to select every flagged episode at
    once, then either set the correct language and reprocess, or
    confirm it's fine to leave the tag undefined.
    </p>

    <div style={{
      display: "flex",
      gap: space.sm,
      alignItems: "center",
      flexWrap: "wrap",
      marginBottom: space.md,
    }}>
    <input
    value={search}
    onChange={e => setSearch(e.target.value)}
    placeholder="Search by filename…"
    style={{
      flex: "1 1 200px",
      padding: `${space.xs}px ${space.md}px`,
      background: palette.bg,
      border: `1px solid ${search ? alpha(SUB_COLOR, ALPHA.half) : palette.border}`,
          borderRadius: radius.sm,
          color: palette.text,
          fontFamily: type.family,
          fontSize: type.size.md,
    }}
    />
    <input
    value={targetLang}
    onChange={e => setTargetLang(e.target.value)}
    placeholder="eng"
    title="ISO 639-2/B language code to apply to selected files"
    style={{
      width: 70,
      padding: `${space.xs}px ${space.sm}px`,
      background: palette.bg,
      border: `1px solid ${palette.border}`,
      borderRadius: radius.sm,
      color: palette.text,
      fontFamily: type.family,
      fontSize: type.size.md,
      textTransform: "lowercase",
    }}
    />
    <Btn
    label={busy ? "WORKING…" : `SET LANGUAGE (${selected.size})`}
    color={palette.green}
    bg={alpha(palette.green, ALPHA.low)}
    onClick={applyLanguage}
    disabled={busy || selected.size === 0 || !targetLang.trim()}
    />
    <Btn
    label={busy ? "WORKING…" : `IGNORE (${selected.size})`}
    color={palette.dim}
    bg="transparent"
    onClick={ignoreSelected}
    disabled={busy || selected.size === 0}
    />
    </div>

    {items.length === 0 && !loading ? (
      <EmptyState msg={
        debouncedSearch
        ? `No flagged files match "${debouncedSearch}"`
        : "No undefined subtitle languages found ✓"
      } />
    ) : (
      <div ref={scrollRef} style={{ maxHeight: 420, overflowY: "auto", border: `1px solid ${palette.border}`, borderRadius: radius.sm }}>
      {items.length > 0 && (
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: space.md,
          padding: `${space.xs}px ${space.lg}px`,
          background: palette.card,
          borderBottom: `1px solid ${palette.border}`,
          position: "sticky",
          top: 0,
        }}>
        <input type="checkbox" checked={allLoadedSelected} onChange={toggleAll} />
        <span style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.normal }}>
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
          gap: space.md,
          padding: `${space.sm}px ${space.lg}px`,
          borderBottom: `1px solid ${palette.border}`,
          cursor: "pointer",
          background: selected.has(item.file_id) ? legacy.rowSelectedBg : "transparent",
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
          color: palette.text,
          fontSize: type.size.md,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>
        {item.filename}
        </span>
        <span style={{
          flexShrink: 0,
          padding: `${space.hair}px ${space.xs}px`,
          background: alpha(palette.yellow, ALPHA.low),
                          border: `1px solid ${alpha(palette.yellow, ALPHA.strong)}`,
                          borderRadius: radius.sm,
                          color: palette.yellow,
                          fontSize: type.size.xs,
                          letterSpacing: type.tracking.wide,
        }}>
        {(item.detected_language || "?").toUpperCase()}
        </span>
        </div>
      ))}

      {hasMore && (
        <div ref={sentinelRef} style={{ padding: `${space.sm}px ${space.lg}px` }}>
        {loading && <span style={{ color: palette.dim, fontSize: type.size.sm }}>Loading…</span>}
        </div>
      )}
      </div>
    )}
    </div>
  );
};
