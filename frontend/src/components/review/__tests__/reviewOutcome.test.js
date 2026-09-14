/**
 * reviewOutcome — what the user is told after resolving a review item.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * Approve, Skip and Resolve reported nothing on success. Three mutants —
 * deleting each handler's error toast — all SURVIVED before this file
 * existed: nothing in the suite asserted that this page ever told the user
 * anything, in either direction.
 *
 * The three statuses are not invented here. Driving approve_manual_review
 * with three stubbed decisions returns status=pending, status=skipped and
 * status=manual_review, each carrying the decision engine's own reason.
 * These tests pin the mapping from that response onto the message.
 *
 * The skipped case is the one that matters most. A file held only by the
 * undefined-audio threshold, already at the target container, is NOT
 * processed when approved — so the message must not congratulate the user
 * on queueing something that will never run.
 */
import { describe, expect, it } from "vitest";

import { reviewOutcome, SKIP_OUTCOME, UNKNOWN_OUTCOME } from "../reviewOutcome";

const TONES = ["success", "error", "warning", "notice", "info", "preview",
               "neutral", "quiet"];

describe("reviewOutcome", () => {
  it("reports a queued file as queued, and says what will happen", () => {
    const { message, tone } = reviewOutcome("pending", "Convert MKV → MP4");

    expect(message).toMatch(/queued/i);
    expect(message).toContain("Convert MKV → MP4");
    expect(tone).toBe("info");
  });

  it("does not claim a skipped file was processed", () => {
    /* The regression this whole thread started from: Approve on a file with
     * nothing else to do leaves it unprocessed. Saying "queued" here would
     * reintroduce the same false claim the card copy used to make. */
    const { message, tone } = reviewOutcome(
      "skipped", "File already meets all configured criteria — no changes needed.");

    expect(message).toMatch(/not changed|nothing left to do/i);
    expect(message).not.toMatch(/queued/i);
    expect(tone).toBe("neutral");
  });

  it("does not promise a conversion while dry run is on", () => {
    /* The status is "pending" in both modes - only is_dry_run separates
     * them - so without this branch a preview gets reported as the real
     * thing and the user believes a file was converted that was not. */
    const { message, tone } = reviewOutcome("pending", "Convert MKV \u2192 MP4", true);

    expect(message).toMatch(/preview/i);
    expect(message).toMatch(/leaves the file alone/i);
    expect(tone).toBe("preview");
  });

  it("treats a missing dry-run flag as a real run", () => {
    /* An older server, or a response that omits the field. Describing a real
     * conversion as a preview is the safer error of the two: it understates
     * what happened rather than claiming a file was left alone when it was
     * not. */
    expect(reviewOutcome("pending", "r").tone).toBe("info");
    expect(reviewOutcome("pending", "r", undefined).tone).toBe("info");
  });

  it.each([
    ["skipped", "neutral"],
    ["manual_review", "notice"],
  ])("does not let dry run change the %s outcome", (status, tone) => {
    /* Nothing was done to the file in either case, in either mode, so these
     * must read identically - a preview tone here would imply output that
     * does not exist. */
    expect(reviewOutcome(status, "r", true)).toEqual(reviewOutcome(status, "r", false));
    expect(reviewOutcome(status, "r", true).tone).toBe(tone);
  });

  it("says when the file is still held rather than going quiet", () => {
    const { message, tone } = reviewOutcome("manual_review", "another gate applies");

    expect(message).toMatch(/still held/i);
    expect(tone).toBe("notice");
  });

  it("falls back rather than throwing on a status it does not know", () => {
    /* A new status added server-side must not take the page down. */
    expect(reviewOutcome("something_new", "x")).toEqual(UNKNOWN_OUTCOME);
    expect(reviewOutcome(undefined, undefined)).toEqual(UNKNOWN_OUTCOME);
  });

  it("leaves no dangling separator when the server sends no reason", () => {
    const { message } = reviewOutcome("pending", "");

    expect(message).not.toMatch(/[—-]\s*$/);
  });

  it("only ever uses tones the theme defines", () => {
    /* An unrecognised tone resolves to amber at render, so a typo would look
     * merely slightly wrong rather than failing — which is exactly the kind
     * of thing that survives review. */
    const outcomes = [
      reviewOutcome("pending", "r"),
      reviewOutcome("skipped", "r"),
      reviewOutcome("manual_review", "r"),
      UNKNOWN_OUTCOME,
      SKIP_OUTCOME,
    ];

    for (const { tone } of outcomes) expect(TONES).toContain(tone);
  });

  it("describes Skip as reversible, since that is what it is", () => {
    /* Skip resets the delta-scan sentinels so the file is re-evaluated —
     * verified in cancel_item. The message says so because the pair is
     * otherwise easy to read the wrong way round. */
    expect(SKIP_OUTCOME.message).toMatch(/next scan/i);
  });
});
