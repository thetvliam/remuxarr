import { useState, useEffect } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { fmtSize, fmtDur } from "../../utils";
import { Stat } from "../atoms/Stat";
import { Btn } from "../atoms/Btn";
import { EmptyState } from "../atoms/EmptyState";
import { AudioLanguageReviewSection } from "./AudioLanguageReviewSection";
import { SubtitleLanguageReviewSection } from "./SubtitleLanguageReviewSection";
import { reviewOutcome, SKIP_OUTCOME } from "./reviewOutcome";

/* ═══════════════════════════════════════════════════════════════════════════
 * MANUAL REVIEW PAGE
 * Everything the pipeline stopped on and wants a decision about. Three
 * surfaces, each with its own backing list:
 *
 * 1. Files that triggered the "multiple undefined audio tracks" gate —
 *    approve (send to queue) or skip (dismiss). The `items` prop.
 *
 * 2. Flagged subtitle tracks on those same files. Three of the four gates
 *    put them here: image-based subtitles that cannot become SRT, embedded
 *    fonts only MKV can hold, and text subtitles FFmpeg could not read as
 *    UTF-8. Resolve one track at a time, or use a bulk action that answers
 *    every item of ONE KIND across every file — there is no per-file bulk,
 *    and encoding items get no bulk action at all. Reads
 *    image_subtitle_handling and font_attachment_handling to phrase what
 *    resolving will do, and to decide whether each button is offered.
 *
 * 3. AudioLanguageReviewSection and SubtitleLanguageReviewSection, rendered
 *    at the bottom: separately paginated, separately filtered lists of
 *    tracks whose language needs confirming. They fetch their own data and
 *    take reviewRefreshKey to know when to refetch.
 ═══════════════════════════════════════════════════════════════════════════ */
/* onRefresh and invalidateHistory are still taken, but only to hand down to
 * the two language sections for their OWN apply actions. Everything this page
 * resolves itself goes through onReviewResolved, which bundles those two with
 * the reviewRefreshKey bump they were each missing. */
export const ReviewPage = ({ api, items, onRefresh, toast, invalidateHistory,
                             reviewRefreshKey = 0, onReviewResolved }) => {
    const { palette, type, space, radius, size, surface } = useTheme();
    const [imgSubSetting, setImgSubSetting] = useState("always_ask");
    const [fontSetting, setFontSetting] = useState("always_ask");
    const [bulkResolving, setBulkResolving] = useState(false);

    useEffect(() => {
        fetch(`${api}/api/settings/image_subtitle_handling`)
        .then(r => r.json())
        .then(data => setImgSubSetting(data.value || "always_ask"))
        .catch(() => {});
        fetch(`${api}/api/settings/font_attachment_handling`)
        .then(r => r.json())
        .then(data => setFontSetting(data.value || "always_ask"))
        .catch(() => {});
    }, [api]);

    /* Two gates flag subtitle tracks, and each is resolved by its own
     * setting. Counting them together would offer to bulk-resolve font
     * items under Image-Based Subtitle Handling, which converts away the
     * styling the review existed to protect — see QueueItem.review_reason.
     *
     * A null reason on a flagged item is never a font review — see
     * QueueItem.review_reason for where those rows come from — and is read
     * as an image-subtitle item here, the same way the bulk resolver reads
     * it.
     *
     * A subtitle-encoding review flags tracks too, but no bulk action may
     * take it: re-deciding the file cannot see the encoding failure, so it
     * would queue the extraction that just failed and the file would come
     * straight back. It is answered one file at a time. */
    const isFontItem = i => i.review_reason === "font_attachments";
    const isEncodingItem = i => i.review_reason === "subtitle_encoding";
    const isImageItem = i => i.flagged_subtitles?.length > 0
        && !isFontItem(i) && !isEncodingItem(i);

    const subtitleItemCount = items.filter(isImageItem).length;
    const fontItemCount = items.filter(isFontItem).length;
    /* Not a bulk-action count like the two above — there is no bulk action
     * for these. It exists only to decide whether to explain why, since a
     * standing note about encoding reviews is noise on a page that has
     * none. */
    const encodingItemCount = items.filter(isEncodingItem).length;

    const resolveAllOfKind = async (endpoint) => {
        setBulkResolving(true);
        try {
            const r = await fetch(`${api}/api/queue/${endpoint}`, { method: "POST" });
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
                onReviewResolved?.();
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

    const resolveAllSubtitles = () => resolveAllOfKind("resolve-subtitles-bulk");
    const resolveAllFonts = () => resolveAllOfKind("resolve-fonts-bulk");

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
        // Approving re-runs the decision engine. If the fresh decision finds
        // nothing to do the item lands on "skipped", a terminal status the
        // Skipped tab displays, which fetchAll never touches — and the
        // re-decide can write a language flag row for the sections below,
        // which only the reviewRefreshKey bump reaches.
        //
        // The status that re-decide produced is the only place the user can
        // learn which of the three outcomes they got, and it is already in
        // the response. Read before the refresh: onReviewResolved removes
        // the card, so reporting after it would describe something gone
        // from the screen.
        const body = await r.json().catch(() => null);
        const { message, tone } = reviewOutcome(body?.status, body?.reason);
        toast?.(message, tone);
        onReviewResolved?.();
    };
    const skip = async (id) => {
        const r = await fetch(`${api}/api/queue/${id}`, { method: "DELETE" }).catch(() => null);
        if (!r?.ok) {
            toast?.("Could not skip — the file is still held for review", "error");
            return;
        }
        // fetchAll alone is blind to useHistoryData's separate refresh
        // mechanism — DELETE here produces a real, terminal "cancelled"
        // status, which the Failed tab's own count already includes, so
        // without this the History panel goes stale until something else
        // happens to trigger a refresh.
        //
        // One outcome, so no classification — but it still has to be said.
        // Skip is the reversible half of the pair and the file comes back;
        // silence made it look as permanent as Approve.
        toast?.(SKIP_OUTCOME.message, SKIP_OUTCOME.tone);
        onReviewResolved?.();
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
        // Same reasoning as skip() above — resolving can move the item to
        // "skipped" or "pending" (later completed/failed), any of which the
        // History panel needs to know about.
        //
        // Branches exactly as approve does: a file with several flagged
        // tracks stays in manual_review until the last one is answered, and
        // "still held" is the outcome most likely to be read as a failure
        // if nothing says otherwise.
        const body = await r.json().catch(() => null);
        const { message, tone } = reviewOutcome(body?.status, body?.reason);
        toast?.(message, tone);
        onReviewResolved?.();
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

        {fontItemCount > 0 && fontSetting !== "always_ask" && (
            <Btn
            label={bulkResolving ? "RESOLVING…" : `RESOLVE ALL ${fontItemCount} FONT ITEMS`}
            color={palette.blue}
            bg={alpha(palette.blue, ALPHA.low)}
            onClick={resolveAllFonts}
            disabled={bulkResolving}
            />
        )}
        </div>
        <p style={{ color: palette.muted, fontSize: type.size.md, margin: 0, lineHeight: type.leading.relaxed }}>
        Files end up here for four reasons: two or more audio tracks with an
        undefined language (approve to process anyway, or skip to dismiss),
            subtitle tracks that can't be converted to external SRT, embedded
            fonts that only MKV can hold, or text subtitles FFmpeg couldn't
            read as UTF-8 — choose KEEP or REMOVE for each flagged track below.
            {subtitleItemCount > 0 && imgSubSetting !== "always_ask" && (
                <> Image-Based Subtitle Handling is currently set to{" "}
                {imgSubSetting === "always_keep" ? "Always Keep" : "Always Remove"} — use
                the button above to resolve every subtitle-flagged item at once instead
                of choosing individually.</>
            )}
            {fontItemCount > 0 && fontSetting !== "always_ask" && (
                <> Embedded Font Handling is currently set to{" "}
                {fontSetting === "always_keep" ? "Always Keep" : "Always Remove"} — use
                the button above to resolve every font-flagged item at once. Keeping
                leaves the file as MKV so its styled subtitles still render.</>
            )}
            {encodingItemCount > 0 && (
                <> The {encodingItemCount === 1 ? "subtitle-encoding one" : `${encodingItemCount} subtitle-encoding ones`} must
                be answered a file at a time — there is no bulk action for them.
                Re-deciding a file cannot see the encoding failure, so resolving
                them in a batch would queue the extraction that just failed and
                bring the file straight back here.</>
            )}
            </p>
            </div>

            {/* Scoped deliberately. This branch only knows about manual-review
              * QUEUE ITEMS, and the two language sections below fetch their
              * own rows independently — a file can carry a language flag
              * without ever entering manual review (one undefined audio track
              * under a threshold of two, for instance, or an undefined
              * subtitle that gets extracted). So items.length === 0 means
              * "nothing in this list", not "nothing on this page". It used to
              * say "all clear ✓", which was a claim about sections this
              * component cannot see and is wrong whenever either has rows. */}
            {items.length === 0
                ? <EmptyState msg="No files pending manual review — flagged languages, if any, are listed below." />
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
                            * line to prevent.
                            *
                            * Approve is also not a promise to process. It only
                            * clears the und-audio gate; whether the file is then
                            * converted depends on whether anything ELSE is
                            * outstanding. A file already at the target container
                            * with nothing to strip comes back "File already meets
                            * all configured criteria" and lands in Skipped. This
                            * line used to say Approve "processes it now, keeping
                            * every audio track", which was wrong for exactly that
                            * case — the one the threshold most often holds. */}
                            {!flagged && (
                                <div style={{
                                    color: palette.dim,
                                    fontSize: type.size.sm,
                                    lineHeight: type.leading.relaxed,
                                    marginTop: space.xs,
                                }}>
                                <b style={{ color: palette.green, fontWeight: type.weight.semibold }}>Approve</b>
                                {" accepts this file's undefined audio tracks and stops asking. It is processed if anything else needs doing, and skipped if not. "}
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
