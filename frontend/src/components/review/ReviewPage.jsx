import { useState, useEffect } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { fmtSize, fmtDur } from "../../utils";
import { Stat } from "../atoms/Stat";
import { Btn } from "../atoms/Btn";
import { EmptyState } from "../atoms/EmptyState";
import { AudioLanguageReviewSection } from "./AudioLanguageReviewSection";
import { SubtitleLanguageReviewSection } from "./SubtitleLanguageReviewSection";

/* ═══════════════════════════════════════════════════════════════════════════
 * MANUAL REVIEW PAGE
 * Lists files that triggered the "multiple undefined audio tracks" gate.
 * User can approve (send to queue) or skip (dismiss).
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */
export const ReviewPage = ({ api, items, onRefresh, toast, invalidateHistory, reviewRefreshKey = 0 }) => {
    const { palette, type, space, radius, size, surface } = useTheme();
    const [imgSubSetting, setImgSubSetting] = useState("always_ask");
    const [bulkResolving, setBulkResolving] = useState(false);

    useEffect(() => {
        fetch(`${api}/api/settings/image_subtitle_handling`)
        .then(r => r.json())
        .then(data => setImgSubSetting(data.value || "always_ask"))
        .catch(() => {});
    }, [api]);

    const subtitleItemCount = items.filter(i => i.flagged_subtitles?.length > 0).length;

    const resolveAllSubtitles = async () => {
        setBulkResolving(true);
        try {
            const r = await fetch(`${api}/api/queue/resolve-subtitles-bulk`, { method: "POST" });
            if (r.ok) {
                const data = await r.json();
                const stillNeeded = data.still_unresolved
                    ? `, ${data.still_unresolved} still needed review`
                    : "";
                toast?.(`Resolved ${data.resolved}${stillNeeded}`, "info");
                // The endpoint commits per item so one bad file cannot roll
                // back the rest, and returns what failed. Discarding that meant
                // a partially-successful bulk resolve reported as a clean one.
                if (Array.isArray(data.errors) && data.errors.length) {
                    console.warn("Bulk resolve — items not resolved:", data.errors);
                    toast?.(
                        `${data.errors.length} item${data.errors.length === 1 ? "" : "s"} could not be resolved — ` +
                        `see the browser console for details`,
                        "error",
                    );
                }
                onRefresh();
                invalidateHistory?.(null);
            } else {
                toast?.("Bulk resolve failed", "error");
            }
        } catch (err) {
            console.error("Bulk resolve failed", err);
            toast?.("Bulk resolve failed", "error");
        } finally {
            setBulkResolving(false);
        }
    };

    /* All three check the response. They previously swallowed everything and
     * refreshed regardless, so a failed Approve looked exactly like a
     * successful one — the card vanished from the list on refresh either
     * way, and the file was left in manual review with the user believing
     * they had cleared it. */
    const approve = async (id) => {
        const r = await fetch(`${api}/api/queue/${id}/approve`, { method: "POST" }).catch(() => null);
        if (!r?.ok) {
            toast?.("Could not approve — the file is still held for review", "error");
            return;
        }
        onRefresh();
        // Same reasoning as skip() and resolveSubtitle() either side of this.
        // Approving re-runs the decision engine, and if the fresh decision
        // finds nothing to do the item lands on "skipped" — a terminal status
        // the Skipped tab displays. onRefresh (fetchAll) never touches history.
        invalidateHistory?.(null);
    };
    const skip = async (id) => {
        const r = await fetch(`${api}/api/queue/${id}`, { method: "DELETE" }).catch(() => null);
        if (!r?.ok) {
            toast?.("Could not skip — the file is still held for review", "error");
            return;
        }
        onRefresh();
        // fetchAll (via onRefresh) is blind to useHistoryData's separate
        // refresh mechanism — DELETE here produces a real, terminal
        // "cancelled" status, which the Failed tab's own count already
        // includes, so without this the History panel goes stale until
        // something else happens to trigger a refresh.
        invalidateHistory?.(null);
    };
    const resolveSubtitle = async (id, streamIndex, choice) => {
        const r = await fetch(`${api}/api/queue/${id}/resolve-subtitles`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ overrides: { [streamIndex]: choice } }),
        }).catch(() => null);
        if (!r?.ok) {
            toast?.("Could not save the subtitle decision", "error");
            return;
        }
        onRefresh();
        // Same reasoning as skip() above — resolving can move the item to
        // "skipped" or "pending" (later completed/failed), any of which
        // the History panel needs to know about.
        invalidateHistory?.(null);
    };

    return (
        <div style={{ maxWidth: 860, margin: "0 auto", padding: `${space.max}px ${space.huge}px` }}>
        {/* Page header */}
        <div style={{ marginBottom: space.huge }}>
        <div style={{ display: "flex", alignItems: "center", gap: space.md, marginBottom: space.sm }}>
        <span style={{ color: palette.yellow, fontSize: type.size.xxl }}>⚠</span>
        <span style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold }}>
        MANUAL REVIEW
        </span>
        <span style={{
            padding: `0 ${space.xs}px`,
            background: alpha(palette.yellow, ALPHA.mild),
            border: `1px solid ${alpha(palette.yellow, ALPHA.strong)}`,
            borderRadius: radius.sm,
            color: palette.yellow,
            fontSize: type.size.xs,
        }}>
        {items.length}
        </span>

        {subtitleItemCount > 0 && imgSubSetting !== "always_ask" && (
            <Btn
            label={bulkResolving ? "RESOLVING…" : `RESOLVE ALL ${subtitleItemCount} SUBTITLE ITEMS`}
            color={palette.blue}
            bg={alpha(palette.blue, ALPHA.low)}
            onClick={resolveAllSubtitles}
            disabled={bulkResolving}
            />
        )}
        </div>
        <p style={{ color: palette.muted, fontSize: type.size.md, margin: 0, lineHeight: type.leading.relaxed }}>
        Files end up here for two reasons: two or more audio tracks with an
        undefined language (approve to process anyway, or skip to dismiss),
            or subtitle tracks that can't be converted to external SRT — choose
            KEEP or REMOVE for each flagged track below.
            {subtitleItemCount > 0 && imgSubSetting !== "always_ask" && (
                <> Image-Based Subtitle Handling is currently set to{" "}
                {imgSubSetting === "always_keep" ? "Always Keep" : "Always Remove"} — use
                the button above to resolve every subtitle-flagged item at once instead
                of choosing individually.</>
            )}
            </p>
            </div>

            {items.length === 0
                ? <EmptyState msg="No files pending manual review — all clear ✓" />
                : items.map(item => {
                    const f = item.file || {};
                    const flagged = item.flagged_subtitles;

                    return (
                        <div
                        key={item.id}
                        style={{
                            padding: `${space.xl}px ${space.xl}px`,
                            background: palette.card,
                            border: `1px solid ${surface.reviewBorder}`,
                            borderRadius: radius.sm,
                            borderLeft: `${size.accentWidth}px solid ${palette.yellow}`,
                            marginBottom: space.md,
                        }}
                        >
                        <div style={{ display: "flex", alignItems: "flex-start", gap: space.xl }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{
                            color: palette.text,
                            fontSize: type.size.lg,
                            fontWeight: type.weight.semibold,
                            marginBottom: space.xxs,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                        }}>
                        {f.filename || "—"}
                        </div>
                        <div style={{
                            color: palette.dim,
                            fontSize: type.size.sm,
                            marginBottom: space.sm,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                        }}>
                        {f.path}
                        </div>
                        <div style={{ color: palette.yellow, fontSize: type.size.md, lineHeight: type.leading.snug }}>
                        {item.reason}
                        </div>
                        {/* The two buttons are not opposites and nothing else
                            * says so: Approve is permanent (it acknowledges the
                            * threshold for this file, so it is never flagged
                            * again), while Skip only cancels this pass and the
                            * file returns on the next scan. Read cold, "Skip"
                            * looks like the cautious, more reversible choice —
                            * it is the other way round, and that is worth one
                            * line to prevent. */}
                            {!flagged && (
                                <div style={{
                                    color: palette.dim,
                                    fontSize: type.size.sm,
                                    lineHeight: type.leading.relaxed,
                                    marginTop: space.xs,
                                }}>
                                <b style={{ color: palette.green, fontWeight: type.weight.semibold }}>Approve</b>
                                {" processes it now, keeping every audio track — and won't ask again. "}
                                <b style={{ color: palette.red, fontWeight: type.weight.semibold }}>Skip</b>
                                {" leaves it untouched; it returns on the next scan."}
                                </div>
                            )}
                            <div style={{ display: "flex", gap: space.xl, marginTop: space.sm }}>
                            <Stat label="SIZE"     value={fmtSize(f.size)} />
                            <Stat label="DURATION" value={fmtDur(f.duration)} />
                            </div>
                            </div>

                            {/* Audio-type review: simple Approve / Skip */}
                            {!flagged && (
                                <div style={{ display: "flex", gap: space.sm, flexShrink: 0, paddingTop: space.hair }}>
                                <Btn label="APPROVE" color={palette.green} bg={alpha(palette.green, ALPHA.low)} onClick={() => approve(item.id)} />
                                <Btn label="SKIP"    color={palette.red}   bg={alpha(palette.red, ALPHA.low)} onClick={() => skip(item.id)} />
                                </div>
                            )}
                            </div>

                            {/* Subtitle-type review: per-track Keep/Remove */}
                            {flagged && flagged.length > 0 && (
                                <div style={{ marginTop: space.lg, borderTop: `1px solid ${palette.border}`, paddingTop: space.lg }}>
                                {flagged.map(track => (
                                    <div
                                    key={track.stream_index}
                                    style={{
                                        display: "flex",
                                        alignItems: "center",
                                        gap: space.lg,
                                        padding: `${space.sm}px ${space.md}px`,
                                        background: surface.trackRowBg,
                                        border: `1px solid ${palette.border}`,
                                        borderRadius: radius.sm,
                                        marginBottom: space.xs,
                                    }}
                                    >
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ color: palette.text, fontSize: type.size.md, fontWeight: type.weight.semibold, marginBottom: space.hair }}>
                                    {track.title || `Stream ${track.stream_index}`}
                                    </div>
                                    <div style={{ display: "flex", gap: space.sm, alignItems: "center" }}>
                                    <span style={{
                                        padding: `${space.hair}px ${space.xs}px`,
                                        background: alpha(palette.yellow, ALPHA.low),
                                                       border: `1px solid ${alpha(palette.yellow, ALPHA.strong)}`,
                                                       borderRadius: radius.sm,
                                                       color: palette.yellow,
                                                       fontSize: type.size.xs,
                                                       letterSpacing: type.tracking.wide,
                                    }}>
                                    {(track.language || "und").toUpperCase()} · {track.codec}
                                    {track.is_forced ? " · FORCED" : ""}
                                    </span>
                                    <span style={{ color: palette.dim, fontSize: type.size.sm }}>stream {track.stream_index}</span>
                                    </div>
                                    </div>
                                    <div style={{ display: "flex", gap: space.sm, flexShrink: 0 }}>
                                    <Btn
                                    label="KEEP"
                                    color={palette.green}
                                    bg={alpha(palette.green, ALPHA.low)}
                                    onClick={() => resolveSubtitle(item.id, track.stream_index, "keep")}
                                    />
                                    <Btn
                                    label="REMOVE"
                                    color={palette.red}
                                    bg={alpha(palette.red, ALPHA.low)}
                                    onClick={() => resolveSubtitle(item.id, track.stream_index, "remove")}
                                    />
                                    </div>
                                    </div>
                                ))}
                                </div>
                            )}
                            </div>
                    );
                })
            }

            <AudioLanguageReviewSection api={api} onRefresh={onRefresh} invalidateHistory={invalidateHistory} reviewRefreshKey={reviewRefreshKey} toast={toast} />
            <SubtitleLanguageReviewSection api={api} onRefresh={onRefresh} invalidateHistory={invalidateHistory} reviewRefreshKey={reviewRefreshKey} toast={toast} />
            </div>
    );
};
