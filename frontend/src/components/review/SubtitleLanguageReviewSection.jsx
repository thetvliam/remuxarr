import { useState, useEffect, useRef } from "react";
import { C } from "../../constants";
import { Btn } from "../atoms/Btn";
import { EmptyState } from "../atoms/EmptyState";
import { usePaginatedFetch } from "../../hooks/usePaginatedFetch";

// Matches the existing extract_subtitle action color in constants.js —
// the established subtitle-related color in this codebase already,
// rather than introducing a new, arbitrary one.
const SUB_COLOR = "#2dd4d4";

/* ═══════════════════════════════════════════════════════════════════════════
 * SUBTITLE LANGUAGE REVIEW SECTION
 * Subtitle counterpart to AudioLanguageReviewSection — identical mechanics
 * (search, multi-select, two bulk actions), mirrored deliberately rather
 * than shared, since the two flag independent things. Every row here
 * originates from an undefined ("und") tag, not a defined-but-wrong one —
 * see fix_undefined_language's "always ask" mode.
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
export const SubtitleLanguageReviewSection = ({ api, onRefresh, setHistoryRefreshKey }) => {
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
    <div style={{ marginTop: 32 }}>
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 10,
      marginBottom: 8,
      paddingTop: 24,
      borderTop: `1px solid ${C.border}`,
    }}>
    <span style={{ color: SUB_COLOR, fontSize: 15 }}>▭</span>
    <span style={{ color: C.dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>
    SUBTITLE LANGUAGE REVIEW
    </span>
    <span style={{
      padding: "0 6px",
      background: SUB_COLOR + "20",
      border: `1px solid ${SUB_COLOR}44`,
      color: SUB_COLOR,
      fontSize: 9,
    }}>
    {total}
    </span>
    </div>
    <p style={{ color: C.muted, fontSize: 11, margin: "0 0 14px", lineHeight: 1.65 }}>
    Files whose kept subtitle track has an undefined language tag,
    flagged because Fix Undefined Language Tags is set to Always Ask.
    These files are already fully processed and playable; this is
    optional. Search a show name to select every flagged episode at
    once, then either set the correct language and reprocess, or
    confirm it's fine to leave the tag undefined.
    </p>

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
      border: `1px solid ${search ? SUB_COLOR + "88" : C.border}`,
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
        : "No undefined subtitle languages found ✓"
      } />
    ) : (
      <div ref={scrollRef} style={{ maxHeight: 420, overflowY: "auto", border: `1px solid ${C.border}` }}>
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
