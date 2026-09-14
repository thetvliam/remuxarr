/**
 * What to tell the user after they resolve a manual-review item.
 *
 * WHY THIS EXISTS
 * ---------------
 * Approving, skipping and resolving a subtitle all reported nothing on
 * success. Only failure produced a toast, so a card simply vanished from the
 * list and the user was left to infer what had happened to the file.
 *
 * The inference was often wrong, because approving is not one outcome. The
 * endpoint re-runs the decision engine and the item lands in one of three
 * places — confirmed by driving approve_manual_review with three stubbed
 * decisions and reading the response:
 *
 *   status=pending        the file was queued, reason names the work
 *   status=skipped        nothing left to do, the file is not processed
 *   status=manual_review  another gate applies, it is STILL held
 *
 * Dry run is read off the SAME response rather than from app state. The
 * status is "pending" whether or not dry-run mode is on — verified by
 * approving with the setting both ways — and only is_dry_run distinguishes
 * them. Taking it from the response also means the toast describes the mode
 * that was in force when the server handled the click, not whatever the
 * client believed a moment later.
 *
 * The middle one is the surprise. A file held only by the undefined-audio
 * threshold, already at the target container with nothing to strip, is
 * skipped rather than converted — see the Approve copy on ReviewPage, which
 * used to claim the opposite.
 *
 * None of this is new information: the server already computes it and
 * already returns it in the serialized item. The frontend read r.ok and
 * threw the body away. This maps what is already on the wire onto what the
 * user is told.
 *
 * SEPARATE MODULE, deliberately. It is pure, so it is testable without
 * rendering anything; all three handlers need the same mapping and inlining
 * it would mean three copies to keep in step; and a batched apply summary,
 * if one is ever built, needs the identical classification per file.
 *
 * Tone names come from buildToastTone in theme.jsx, which documents what
 * each is for. They are not free-form: an unrecognised tone falls back to
 * amber at render, so a typo looks slightly wrong rather than throwing.
 */

/** Fallback used when the server reports a status this map does not know. */
export const UNKNOWN_OUTCOME = {
    message: "Review item resolved.",
    tone: "info",
};

export function reviewOutcome(status, reason, isDryRun) {
    /* The reason is the decision engine's own text ("Convert MKV → MP4",
     * "File already meets all configured criteria — no changes needed"), so
     * the toast can say why rather than paraphrase it. It is appended only
     * when present: an empty reason must not leave a dangling separator. */
    const because = reason ? ` — ${reason}` : "";

    switch (status) {
        /* "neutral progress: queued" is exactly this case, per buildToastTone.
         *
         * Dry run splits it, and ONLY it. The status is "pending" either way —
         * is_dry_run on the response carries the whole difference — so without
         * this branch the toast promises a conversion that will not happen and
         * a preview gets reported as the real thing. The other two cases do not
         * move: "skipped" means nothing was done in either mode, and
         * "manual_review" means the file is still held in either mode.
         *
         * `preview` is its own tone in buildToastTone precisely so dry-run
         * output does not read as an ordinary success. */
        case "pending":
            return isDryRun
                ? { message: `Approved — dry run, so this writes a preview and leaves the file alone${because}`,
                    tone: "preview" }
                : { message: `Approved and queued${because}`, tone: "info" };

        /* Not a failure, so not an error tone — but the user expected the file
         * to be processed, so it cannot be silent either. */
        case "skipped":
            return { message: `Nothing left to do — the file was not changed${
                reason ? `. ${reason}` : ""}`, tone: "neutral" };

        /* The action succeeded and the file is still on this page. Saying
         * nothing here is the worst case of the three: the card disappears on
         * refresh and reappears, looking like a bug. */
        case "manual_review":
            return { message: `Still held for review${because}`, tone: "notice" };

        default:
            return UNKNOWN_OUTCOME;
    }
}

/**
 * Skipping has one outcome. DELETE always produces a terminal "cancelled"
 * item, so there is nothing to classify — this exists so the three handlers
 * report through one place rather than two plus an inline string.
 */
export const SKIP_OUTCOME = {
    message: "Skipped — the file is unchanged and returns on the next scan.",
    tone: "neutral",
};
