import { useCallback, useEffect, useRef, useState } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { Btn } from "../atoms/Btn";
import { EmptyState } from "../atoms/EmptyState";
import { ReviewCard } from "./ReviewCard";
import { AudioLanguageReviewSection } from "./AudioLanguageReviewSection";
import { SubtitleLanguageReviewSection } from "./SubtitleLanguageReviewSection";
import { AcknowledgedSection } from "./AcknowledgedSection";

/* ═══════════════════════════════════════════════════════════════════════════
 * MANUAL REVIEW PAGE
 *
 * Everything the pipeline stopped on and wants a decision about. Four
 * surfaces, each with its own backing list:
 *
 * 1. The subtitle questions, as cards. One release of one show is twelve
 *    files with the same tracks, so it is one question, asked once and
 *    answered for all of them. Loaded from /api/queue/review/groups, which
 *    pages by card: a page boundary through the middle of a card cannot be
 *    rendered honestly, because the count on it would be of whatever
 *    happened to fit.
 *
 * 2. Nothing is written while choosing. Answers, per-file exceptions and
 *    skips are staged here and sent together by Apply, so a page of
 *    decisions is one request and one summary rather than a toast per file.
 *    Until then every choice is undone by making another.
 *
 * 3. AudioLanguageReviewSection and SubtitleLanguageReviewSection, rendered
 *    below: separately paginated, separately filtered lists of tracks whose
 *    language needs confirming. They fetch their own data and take
 *    reviewRefreshKey to know when to refetch.
 *
 * 4. AcknowledgedSection, last and hidden when empty: files whose undefined
 *    audio tracks have been confirmed correct. Unlike 1-3 it lists decisions
 *    already made rather than work waiting, which is why it stays out of the
 *    way when there are none.
 *
 * The outcome line under a card's choices is not worked out here. Keeping a
 * track holds a file as MKV, but so does DTS audio, and a file already in
 * MP4 converts to nothing: checked against the decision engine, the rule
 * "any keep means it stays MKV" was wrong for six of seven files. The server
 * answers that for the staged choices, through /api/queue/review/preview.
 * ═══════════════════════════════════════════════════════════════════════════ */

const PAGE_SIZE = 25;
const PREVIEW_DEBOUNCE_MS = 250;

/* A card's answer for one file: the card's choices with that file's own
 * exceptions on top, keyed by the stream numbers THAT file has. The slots are
 * shared across the card; the numbers behind them are not, which is why the
 * endpoints take answers per file. */
export const answersForFile = (file, answers, fileAnswers) => {
    const own = { ...answers, ...(fileAnswers || {}) };
    const out = {};
    (file.streams || []).forEach((streamIndex, slot) => {
        if (own[slot] !== undefined) out[streamIndex] = own[slot];
    });
    return out;
};

const blank = () => ({ answers: {}, files: {}, skipped: false });

const isDecided = (group, staged) =>
    !staged?.skipped
    && (group.tracks || []).every((_t, slot) => staged?.answers?.[slot] !== undefined);

/** What Apply would do to this card, from the per-file outcomes the server returned. */
export const outcomeLine = (group, staged, previewed) => {
    if (!previewed || previewed.length === 0) return null;

    const converting = previewed.filter(o => o.target_container === "mp4").length;
    const total = previewed.length;
    const container =
        converting === total ? "Converts to MP4"
        : converting === 0   ? "Stays MKV"
        : `Converts ${converting} of ${total} to MP4, ${total - converting} stay MKV`;

    const answers = staged?.answers || {};
    const count = choice => Object.values(answers).filter(c => c === choice).length;
    const parts = [
        count("keep")    && `${count("keep")} kept`,
        count("extract") && `${count("extract")} to SRT`,
        count("remove")  && `${count("remove")} deleted`,
    ].filter(Boolean);

    let line = parts.length ? `${container}, ${parts.join(", ")}` : container;
    /* Deleting a track while another is kept reads as though it changes the
     * outcome. It does not: the kept track is what holds the container, so
     * the line says so rather than leaving it to be inferred. */
    if (converting === 0 && count("keep") > 0 && count("remove") > 0) {
        line += ". Deleting the rest won't change the container.";
    }
    return line;
};

export const ReviewPage = ({ api, onRefresh, toast, invalidateHistory,
                             reviewRefreshKey = 0, onReviewResolved }) => {
    const { palette, type, space, radius } = useTheme();

    const [groups, setGroups] = useState([]);
    const [totalGroups, setTotalGroups] = useState(0);
    const [totalFiles, setTotalFiles] = useState(0);
    const [loading, setLoading] = useState(false);
    const [staged, setStaged] = useState({});      // card key -> { answers, files, skipped }
    const [outcomes, setOutcomes] = useState({});  // card key -> sentence
    const [applying, setApplying] = useState(false);

    const sentinelRef = useRef(null);
    const hasMore = groups.length < totalGroups;

    const loadPage = useCallback(async (offset) => {
        setLoading(true);
        try {
            const r = await fetch(
                `${api}/api/queue/review/groups?limit=${PAGE_SIZE}&offset=${offset}`);
            const data = await r.json();
            setGroups(prev => offset === 0
                ? (data.groups || [])
                : [...prev, ...(data.groups || [])]);
            setTotalGroups(data.total_groups || 0);
            setTotalFiles(data.total_files || 0);
        } catch (err) {
            console.error("Failed to load review cards", err);
        } finally {
            setLoading(false);
        }
    }, [api]);

    /* Staging is dropped on a refresh on purpose: the cards it belonged to
     * may not be the same cards any more, and an answer carried across that
     * would answer a question nobody was shown. */
    useEffect(() => {
        setStaged({});
        setOutcomes({});
        loadPage(0);
    }, [loadPage, reviewRefreshKey]);

    useEffect(() => {
        const sentinel = sentinelRef.current;
        if (!sentinel || !hasMore || loading) return;
        const observer = new IntersectionObserver(
            ([entry]) => { if (entry.isIntersecting) loadPage(groups.length); },
            { threshold: 0 },
        );
        observer.observe(sentinel);
        return () => observer.disconnect();
    }, [hasMore, loading, groups.length, loadPage]);

    // ── Staging ───────────────────────────────────────────────────────────────
    const choose = (key, slot, choice) => setStaged(prev => {
        const card = { ...blank(), ...prev[key] };
        return { ...prev, [key]: { ...card, answers: { ...card.answers, [slot]: choice } } };
    });

    const chooseForFile = (key, fileId, slot, choice) => setStaged(prev => {
        const card = { ...blank(), ...prev[key] };
        const own = { ...(card.files[fileId] || {}), [slot]: choice };
        return { ...prev, [key]: { ...card, files: { ...card.files, [fileId]: own } } };
    });

    const revertFileToCard = (key, fileId) => setStaged(prev => {
        const card = { ...blank(), ...prev[key] };
        const files = { ...card.files };
        delete files[fileId];
        return { ...prev, [key]: { ...card, files } };
    });

    const toggleSkip = key => setStaged(prev => {
        const card = { ...blank(), ...prev[key] };
        return { ...prev, [key]: { ...card, skipped: !card.skipped } };
    });

    // ── The outcome line, from the engine ─────────────────────────────────────
    useEffect(() => {
        const decided = groups.filter(g => isDecided(g, staged[g.key]));
        if (decided.length === 0) return;

        const timer = setTimeout(async () => {
            for (const group of decided) {
                const card = staged[group.key];
                const body = {
                    files: group.files.map(f => ({
                        file_id: f.file_id,
                        answers: answersForFile(f, card.answers, card.files[f.file_id]),
                    })),
                };
                try {
                    const r = await fetch(`${api}/api/queue/review/preview`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body),
                    });
                    const data = await r.json();
                    setOutcomes(prev => ({
                        ...prev,
                        [group.key]: outcomeLine(group, card, data.outcomes),
                    }));
                } catch (err) {
                    /* No line rather than a guessed one. A sentence the engine
                     * did not produce is the thing this endpoint exists to stop
                     * being invented. */
                    console.error("Failed to preview review decisions", err);
                    setOutcomes(prev => ({ ...prev, [group.key]: null }));
                }
            }
        }, PREVIEW_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [api, groups, staged]);

    // ── Apply ─────────────────────────────────────────────────────────────────
    const decidedCards = groups.filter(g => isDecided(g, staged[g.key]));
    const skippedCards = groups.filter(g => staged[g.key]?.skipped);
    const decidedFiles = decidedCards.reduce((n, g) => n + g.files.length, 0);
    const skippedFiles = skippedCards.reduce((n, g) => n + g.files.length, 0);

    const apply = async () => {
        setApplying(true);
        const body = {
            files: decidedCards.flatMap(g => g.files.map(f => ({
                file_id: f.file_id,
                answers: answersForFile(f, staged[g.key].answers,
                                        staged[g.key].files[f.file_id]),
            }))),
            skips: skippedCards.flatMap(g => g.files.map(f => f.file_id)),
        };
        try {
            const r = await fetch(`${api}/api/queue/review/apply`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            const data = await r.json();
            if (!r.ok) throw new Error("Apply failed");

            /* Counted from what the server reported, not from what was sent: a
             * file whose tracks changed under the page is refused, and calling
             * that applied would report the request rather than the result. */
            const applied = (data.outcomes || []).length;
            const refused = (data.errors || []).length;
            toast?.(
                refused
                    ? `${applied} ${applied === 1 ? "file" : "files"} answered, ${refused} could not be`
                    : `${applied} ${applied === 1 ? "file" : "files"} answered`,
                refused ? "warning" : "success",
            );
            setStaged({});
            setOutcomes({});
            await loadPage(0);
            onReviewResolved?.();
        } catch (err) {
            console.error("Failed to apply review decisions", err);
            toast?.("Could not apply these decisions", "error");
        } finally {
            setApplying(false);
        }
    };

    const applyLabel = applying
        ? "Applying…"
        : skippedFiles
            ? `Apply: ${decidedFiles} decided, ${skippedFiles} skipped`
            : `Apply: ${decidedFiles} decided`;

    return (
        <div style={{ maxWidth: 860, margin: "0 auto", padding: `${space.max}px ${space.huge}px` }}>

        <div style={{ marginBottom: space.huge }}>
        <div style={{ display: "flex", alignItems: "center", gap: space.md, marginBottom: space.sm }}>
        <span style={{ color: palette.yellow, fontSize: type.size.xxl }}>⚠</span>
        <span style={{ color: palette.dim, fontSize: type.size.xs,
            letterSpacing: type.tracking.max, fontWeight: type.weight.bold }}>
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
        {totalFiles}
        </span>
        </div>
        <p style={{ color: palette.muted, fontSize: type.size.md, margin: 0,
            lineHeight: type.leading.relaxed }}>
        Subtitle tracks that cannot be converted to external SRT, embedded
        fonts only MKV can hold, and text subtitles FFmpeg could not read.
        Files with the same tracks in the same folder are one card: choose
        once and it applies to all of them. Nothing changes until you press
        Apply.
        </p>
        </div>

        {groups.length === 0 && !loading
            ? <EmptyState msg="No subtitle questions waiting — flagged languages, if any, are listed below." />
            : groups.map(group => (
                <ReviewCard
                key={group.key}
                group={group}
                answers={staged[group.key]?.answers}
                fileAnswers={staged[group.key]?.files}
                skipped={!!staged[group.key]?.skipped}
                outcome={outcomes[group.key]}
                onChoose={(slot, choice) => choose(group.key, slot, choice)}
                onChooseForFile={(fileId, slot, choice) =>
                    chooseForFile(group.key, fileId, slot, choice)}
                onUseGroupChoice={fileId => revertFileToCard(group.key, fileId)}
                onToggleSkip={() => toggleSkip(group.key)}
                />
            ))
        }

        <div ref={sentinelRef} style={{ height: 1 }} />

        {(decidedFiles > 0 || skippedFiles > 0) && (
            <div style={{
                position: "sticky",
                bottom: 0,
                display: "flex",
                justifyContent: "flex-end",
                padding: `${space.md}px 0`,
                background: palette.bg,
            }}>
            <Btn
            label={applyLabel}
            color={palette.green}
            bg={alpha(palette.green, ALPHA.low)}
            onClick={apply}
            disabled={applying}
            />
            </div>
        )}

        <AudioLanguageReviewSection api={api} onRefresh={onRefresh}
        invalidateHistory={invalidateHistory} reviewRefreshKey={reviewRefreshKey} toast={toast} />
        <SubtitleLanguageReviewSection api={api} onRefresh={onRefresh}
        invalidateHistory={invalidateHistory} reviewRefreshKey={reviewRefreshKey} toast={toast} />
        {/* Last, and hidden when empty. It is a record of past decisions
          * rather than work waiting, so it must not push the two language
          * lists down the page on the libraries that have none. */}
        <AcknowledgedSection api={api} refreshKey={reviewRefreshKey} toast={toast} />
        </div>
    );
};
