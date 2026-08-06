import { useState, useEffect, useRef } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { Btn } from "../atoms/Btn";
import { EmptyState } from "../atoms/EmptyState";
import { usePaginatedFetch } from "../../hooks/usePaginatedFetch";

/* ═══════════════════════════════════════════════════════════════════════════
 * LANGUAGE REVIEW SECTION
 *
 * Drives both the audio and subtitle review lists. They were separate files
 * that had drifted to 97% identical code — every mechanic below (debounce,
 * server-side filtering, facet retention, the zero-count option fallback,
 * selection clearing, the infinite-scroll sentinel, both bulk handlers and
 * the entire render tree) existed twice, differing only in an endpoint, an
 * accent colour, a glyph and four strings.
 *
 * The old header argued the mirroring was deliberate because the two flag
 * independent things. That is true of the DATA and stays true: each mount
 * has its own endpoint, its own refresh key, its own selection and its own
 * paginated list, and nothing is shared at runtime. It was never an argument
 * for duplicating the markup, and the cost had started to show — the error
 * handling for a failed bulk action had to be written twice, and the
 * rationale for facet retention was spelled out in one copy and left
 * unexplained in the other.
 *
 * Everything that genuinely differs between the two is a prop.
 ═ * *══════════════════════════════════════════════════════════════════════════ */
export const LanguageReviewSection = ({
    api,
    onRefresh,
    setHistoryRefreshKey,
    reviewRefreshKey = 0,
    toast,
    // ── per-flavour configuration ──────────────────────────────────────────
    endpoint,     // "/api/audio-language-review/" — also the base for the
    // apply and ignore actions
    accent,       // palette colour the whole section is tinted with
    glyph,        // single character beside the heading
    heading,      // "AUDIO LANGUAGE REVIEW"
    blurb,        // explanatory paragraph under the heading
    filterTitle,  // tooltip on the language dropdown
    emptyMessage, // shown when nothing is flagged at all
    trackNoun,    // "audio language" / "subtitle language", used in error toasts
}) => {
    const { palette, type, space, radius, surface } = useTheme();
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
    // Two independent refresh signals, combined into one value because the
    // shared hook takes a single key. `refreshKey` is local, bumped after
    // this section's own Apply/Ignore. `reviewRefreshKey` comes from the
    // WebSocket layer and fires when a scan, a webhook-queued file or a
    // finished job may have written new flag rows — without it, a scan could
    // surface twenty new mismatches while this list kept showing whatever it
    // fetched on mount, until the page was navigated away from and back.
    const combinedKey = `${reviewRefreshKey}:${refreshKey}`;

    const { items, total, loading, hasMore, loadMore, raw } = usePaginatedFetch(
        api, endpoint, combinedKey, debouncedSearch, 100,
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
        }, [debouncedSearch, language, combinedKey]);

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
                // Both branches matter. Without them a 500, a 422 or a dropped
                // connection produced exactly the success path: the list refreshed,
                // the selection cleared, and the user was left believing every
                // selected file had been re-tagged.
                const r = await fetch(`${api}${endpoint}apply`, {
                    method:  "POST",
                    headers: { "Content-Type": "application/json" },
                    body:    JSON.stringify({ file_ids: Array.from(selected), target_language: lang }),
                });
                if (!r.ok) {
                    toast?.(`Failed to set ${trackNoun} on ${selected.size} file${selected.size === 1 ? "" : "s"}`, "error");
                    return;
                }
                // Confirmed, not just refreshed. Failure was reported and success
                // was not, so the only signal either way was the list emptying —
                // which is also what a no-op looks like. Count captured before
                // the refresh clears the selection.
                const applied = selected.size;
                toast?.(`Set ${trackNoun} to ${lang.toUpperCase()} on ${applied} file${applied === 1 ? "" : "s"}`, "success");
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
            } catch (err) {
                console.error("Language review: resolve request failed", err);
                // A rejected fetch (offline, DNS, connection reset) never reaches the
                // !r.ok check above, and without this escapes as an unhandled promise
                // rejection while the UI shows nothing at all.
                toast?.("Could not reach the server", "error");
            } finally {
                setBusy(false);
            }
        };

        const ignoreSelected = async () => {
            if (selected.size === 0) return;
            setBusy(true);
            try {
                const r = await fetch(`${api}${endpoint}ignore`, {
                    method:  "POST",
                    headers: { "Content-Type": "application/json" },
                    body:    JSON.stringify({ file_ids: Array.from(selected) }),
                });
                if (!r.ok) {
                    toast?.("Failed to ignore the selected files", "error");
                    return;
                }
                const ignored = selected.size;
                toast?.(`Ignoring ${ignored} file${ignored === 1 ? "" : "s"} — they won't be flagged again`, "neutral");
                /* Only the local refreshKey, unlike applyLanguage which also calls
                 * onRefresh() and bumps the History key. That asymmetry is
                 * deliberate: applying a correction deletes the file's QueueItem
                 * and creates a new one, which the dashboard queue and History
                 * tabs both need to know about. Ignoring only writes an override
                 * row — no queue item changes hands, so there is nothing for
                 * those views to re-read. */
                setRefreshKey(k => k + 1);
            } catch (err) {
                console.error("Language review: ignore request failed", err);
                // A rejected fetch (offline, DNS, connection reset) never reaches the
                // !r.ok check above, and without this escapes as an unhandled promise
                // rejection while the UI shows nothing at all.
                toast?.("Could not reach the server", "error");
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
            <span style={{ color: accent, fontSize: type.size.xxl }}>{glyph}</span>
            <span style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold }}>
            {heading}
            </span>
            <span style={{
                padding: `0 ${space.xs}px`,
                background: alpha(accent, ALPHA.mild),
                border: `1px solid ${alpha(accent, ALPHA.strong)}`,
                borderRadius: radius.sm,
                color: accent,
                fontSize: type.size.xs,
            }}>
            {total}
            </span>
            </div>
            <p style={{ color: palette.muted, fontSize: type.size.md, margin: `0 0 ${space.xl}px`, lineHeight: type.leading.relaxed }}>
            {blurb}
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
                border: `1px solid ${search ? alpha(accent, ALPHA.half) : palette.border}`,
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
                title={filterTitle}
                style={{
                    padding: `${space.xs}px ${space.sm}px`,
                    background: palette.bg,
                    border: `1px solid ${language ? alpha(accent, ALPHA.half) : palette.border}`,
                borderRadius: radius.sm,
                color: language ? accent : palette.text,
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
                        : emptyMessage
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
                        /* A <label> rather than a div with onClick. The row's only job is to
                         * toggle the checkbox inside it, and a label does that natively: the
                         * whole row becomes the click target, the row text becomes the
                         * checkbox's accessible name, and there is exactly one tab stop.
                         *
                         * role="button" plus tabIndex would have been worse — a second tab stop
                         * per row doing the same thing as the checkbox beside it, and a name
                         * that has to be written by hand and kept in step with the text. */
                        <label
                        key={item.id}
                        style={{
                            display: "flex",
                            alignItems: "center",
                            gap: space.md,
                            padding: `${space.sm}px ${space.lg}px`,
                            borderBottom: `1px solid ${palette.border}`,
                            cursor: "pointer",
                            background: selected.has(item.file_id) ? surface.rowSelectedBg : "transparent",
                        }}
                        >
                        <input
                        type="checkbox"
                        checked={selected.has(item.file_id)}
                        onChange={() => toggleOne(item.file_id)}
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
                        </label>
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
