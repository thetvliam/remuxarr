import { useState, useEffect, useRef } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { Btn } from "../atoms/Btn";
import { EmptyState } from "../atoms/EmptyState";
import { usePaginatedFetch } from "../../hooks/usePaginatedFetch";

/* ═══════════════════════════════════════════════════════════════════════════
 * AUDIO LANGUAGE REVIEW SECTION
 * Self-contained: search, multi-select, and two bulk actions. Distinct
 * from the manual-review list above it — files here are already fully
 * processed and playable; this is purely an optional correction workflow.
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */
export const AudioLanguageReviewSection = ({ api, onRefresh, setHistoryRefreshKey }) => {
    const { palette, type, space, radius, legacy } = useTheme();
    const [search,          setSearch]          = useState("");
    const [debouncedSearch, setDebouncedSearch] = useState("");
    const [language,        setLanguage]        = useState("");
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
    //
    // The language filter goes to the server for the same reason the search
    // does: the list is paginated, so filtering the loaded page would report
    // fewer matches than exist and the bulk actions below would then act on
    // a subset the user believes is the whole set.
    const { items, total, loading, hasMore, loadMore, raw } = usePaginatedFetch(
        api, "/api/audio-language-review/", refreshKey, debouncedSearch, 100,
        { language },
    );

    // The server sends language counts with every page. Hold the last set
    // rather than reading them straight off the response: the response is
    // null while a fetch is in flight, which would empty the dropdown on
    // every keystroke and every filter change — including the change that
    // is currently being made, so the control would lose its own options
    // mid-interaction.
    const [facets, setFacets] = useState([]);
    useEffect(() => {
        if (raw?.languages) setFacets(raw.languages);
    }, [raw]);

        // A selected language that the current search has no files for would
        // otherwise vanish from the options, and a <select> whose value is not
        // among its options renders blank — leaving the filter applied with the
        // control showing no sign of it. Keep it listed at zero so the state is
        // always visible and always clearable.
        const languageOptions = language && !facets.some(f => f.language === language)
        ? [...facets, { language, count: 0 }]
        : facets;

        // Clear selection whenever the underlying list changes shape (new
        // search, new language filter, or a refresh after an action) — stale
        // selected IDs pointing at items no longer shown would be confusing to
        // act on, and the bulk actions would apply to files that are no longer
        // on screen.
        useEffect(() => {
            setSelected(new Set());
        }, [debouncedSearch, language, refreshKey]);

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
            <div style={{ marginTop: space.xxxl }}>
            {/* Section header — visually distinct from the manual-review list above */}
            <div style={{
                display: "flex",
                alignItems: "center",
                gap: space.md,
                marginBottom: space.sm,
                paddingTop: space.huge,
                borderTop: `1px solid ${palette.border}`,
            }}>
            <span style={{ color: palette.blue, fontSize: type.size.xxl }}>♪</span>
            <span style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold }}>
            AUDIO LANGUAGE REVIEW
            </span>
            <span style={{
                padding: `0 ${space.xs}px`,
                background: alpha(palette.blue, ALPHA.mild),
                border: `1px solid ${alpha(palette.blue, ALPHA.strong)}`,
                borderRadius: radius.sm,
                color: palette.blue,
                fontSize: type.size.xs,
            }}>
            {total}
            </span>
            </div>
            <p style={{ color: palette.muted, fontSize: type.size.md, margin: `0 0 ${space.xl}px`, lineHeight: type.leading.relaxed }}>
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
                border: `1px solid ${search ? alpha(palette.blue, ALPHA.half) : palette.border}`,
                borderRadius: radius.sm,
                color: palette.text,
                fontFamily: type.family,
                fontSize: type.size.md,
            }}
            />
            {/* Language filter. A dropdown rather than a text box because the
                * useful question here is "which wrong tags do I actually have?" —
                * the user usually cannot guess that, and a free-text field would
                * mostly return nothing. The counts make the bulk-fixable ones
                * obvious at a glance. */}
                <select
                value={language}
                onChange={e => setLanguage(e.target.value)}
                title="Filter by the language tag currently on the file"
                style={{
                    padding: `${space.xs}px ${space.sm}px`,
                    background: palette.bg,
                    border: `1px solid ${language ? alpha(palette.blue, ALPHA.half) : palette.border}`,
                borderRadius: radius.sm,
                color: language ? palette.blue : palette.text,
                fontFamily: type.family,
                fontSize: type.size.md,
                cursor: "pointer",
                }}
                >
                <option value="">All languages</option>
                {languageOptions.map(f => (
                    <option key={f.language} value={f.language}>
                    {f.language} ({f.count})
                    </option>
                ))}
                </select>
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
                        debouncedSearch && language
                        ? `No files tagged "${language}" match "${debouncedSearch}"`
                        : language
                        ? `No files tagged "${language}"`
                        : debouncedSearch
                        ? `No flagged files match "${debouncedSearch}"`
                        : "No audio language mismatches found ✓"
                    } />
                ) : (
                    <div ref={scrollRef} style={{ maxHeight: 420, overflowY: "auto", border: `1px solid ${palette.border}`, borderRadius: radius.sm }}>
                    {/* Select-all header row */}
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
