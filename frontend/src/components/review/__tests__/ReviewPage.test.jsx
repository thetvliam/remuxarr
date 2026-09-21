/**
 * The review page: cards in, one Apply out.
 *
 * Nothing is written while a person is choosing. Answers, per-file
 * exceptions and skips are staged here and sent together, so what these
 * tests pin is what Apply carries, what the button claims it carries, and
 * what the summary says came back.
 *
 * The old page resolved one item at a time and its tests went with it. Every
 * mutation below survived the suite as it stood before these were written:
 *
 *   • answers sent by slot rather than by each file's stream numbers
 *   • a file's own exception ignored, or applied to every file
 *   • skipped cards left out of the request
 *   • a card with an unanswered track sent anyway
 *   • the button counting cards instead of files, or dropping the skipped count
 *   • the summary counting what was sent rather than what came back
 *   • the outcome line derived on the page instead of from the preview
 *   • staged answers surviving a refresh
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewPage } from "../ReviewPage";

const API = "http://x";

/* Two files whose tracks sit at different stream numbers: the card's slots
 * are shared, the numbers behind them are not. */
const GROUP = {
  key: "g1",
  heading: "Show / Season 1",
  directory: "/media/tv/Show/Season 1",
  file_count: 2,
  font_attachments: 17,
  tracks: [
    { codec: "ass", language: "jpn", is_forced: false, reason: "styled", title: "Signs" },
    { codec: "hdmv_pgs_subtitle", language: "eng", is_forced: false, reason: "image", title: null },
  ],
  files: [
    { file_id: 1, filename: "ep01.mkv", path: "/media/tv/Show/Season 1/ep01.mkv", streams: [2, 3] },
    { file_id: 2, filename: "ep02.mkv", path: "/media/tv/Show/Season 1/ep02.mkv", streams: [4, 5] },
  ],
};

const SECOND = { ...GROUP, key: "g2", heading: "Other / Season 2", file_count: 1,
                 files: [{ file_id: 3, filename: "x.mkv", path: "/x.mkv", streams: [2, 3] }] };

let posted;
let toast;

function mockApi({ groups = [GROUP], applyBody = null, applyOk = true,
                   previewContainer = "mp4", currentContainer = "mkv",
                   blockedBeyondSubtitles = false,
                   // Per file, for cards whose files do not all end the same way.
                   previewPerFile = null } = {}) {
  posted = [];
  toast = vi.fn();
  global.fetch = vi.fn(async (url, opts = {}) => {
    const u = String(url);
    if (opts.method === "POST") {
      posted.push({ url: u, body: JSON.parse(opts.body) });
      if (u.includes("/review/preview")) {
        return { ok: true, json: async () => ({
          outcomes: JSON.parse(opts.body).files.map(f => ({
            file_id: f.file_id,
            current_container: currentContainer,
            target_container: previewContainer,
            will_process: true, still_in_review: false,
            blocked_beyond_subtitles: blockedBeyondSubtitles,
            ...(previewPerFile?.[f.file_id] || {}),
          })),
          errors: [],
        }) };
      }
      return {
        ok: applyOk,
        json: async () => applyBody
          ?? { outcomes: JSON.parse(opts.body).files.map(f => ({ file_id: f.file_id })),
               errors: [] },
      };
    }
    if (u.includes("/review/groups")) {
      return { ok: true, json: async () => ({
        groups, total_groups: groups.length,
        total_files: groups.reduce((n, g) => n + g.file_count, 0),
      }) };
    }
    return { ok: true, json: async () => ({ items: [], total: 0, files: [] }) };
  });
}

class FakeObserver {
  observe() {}
  disconnect() {}
}

beforeEach(() => {
  vi.stubGlobal("IntersectionObserver", FakeObserver);
});

const show = () => render(<ReviewPage api={API} toast={toast} onReviewResolved={() => {}} />);

/**
 * Answer both tracks of the FIRST card.
 *
 * By index rather than by the last match: the cards render in order, so the
 * first two Deletes are this card's two tracks and the later ones belong to
 * whatever card comes next.
 */
async function answerCard(user) {
  await screen.findByText("Show / Season 1");
  const deletes = () => screen.getAllByRole("button", { name: "Delete" });
  await user.click(deletes()[0]);
  await user.click(deletes()[1]);
}

const applyButton = () => screen.getByRole("button", { name: /^Apply/ });
const applyRequest = () => posted.find(p => p.url.includes("/review/apply"))?.body;

describe("what Apply carries", () => {
  it("sends each file's own stream numbers, not the card's slots", async () => {
    const user = userEvent.setup();
    mockApi();
    show();

    await answerCard(user);
    await user.click(applyButton());

    await waitFor(() => expect(applyRequest()).toBeTruthy());
    expect(applyRequest().files).toEqual([
      { file_id: 1, answers: { 2: "remove", 3: "remove" } },
      { file_id: 2, answers: { 4: "remove", 5: "remove" } },
    ]);
  });

  it("sends a file's own answer only for that file", async () => {
    const user = userEvent.setup();
    mockApi();
    show();

    await answerCard(user);
    await user.click(screen.getByRole("button", { name: /Show files/ }));
    await user.click(screen.getByRole("button", { name: /ep02.mkv/ }));
    await user.click(screen.getAllByRole("button", { name: "Keep" }).at(-1));
    await user.click(applyButton());

    await waitFor(() => expect(applyRequest()).toBeTruthy());
    expect(applyRequest().files).toEqual([
      { file_id: 1, answers: { 2: "remove", 3: "remove" } },
      { file_id: 2, answers: { 4: "remove", 5: "keep" } },
    ]);
  });

  it("sends skipped cards as skips, and leaves undecided ones out", async () => {
    const user = userEvent.setup();
    mockApi({ groups: [GROUP, SECOND] });
    show();

    await screen.findByText("Other / Season 2");
    // The first card is skipped; the second is left half-answered.
    await user.click(screen.getAllByRole("button", { name: "Skip" })[0]);
    await user.click(screen.getAllByRole("button", { name: "Keep" }).at(0));
    await user.click(applyButton());

    await waitFor(() => expect(applyRequest()).toBeTruthy());
    expect(applyRequest()).toEqual({ files: [], skips: [1, 2] });
  });
});

describe("what the page says", () => {
  it("counts files, not cards, and names the skipped ones separately", async () => {
    const user = userEvent.setup();
    mockApi({ groups: [GROUP, SECOND] });
    show();

    await answerCard(user);
    expect(applyButton()).toHaveTextContent("Apply: 2 decided");

    await user.click(screen.getAllByRole("button", { name: "Skip" })[1]);
    expect(applyButton()).toHaveTextContent("Apply: 2 decided, 1 skipped");
  });

  it("offers nothing to apply until something is staged", async () => {
    mockApi();
    show();

    await screen.findByText("Show / Season 1");
    expect(screen.queryByRole("button", { name: /^Apply/ })).toBeNull();
  });

  it("summarises what came back, not what was sent", async () => {
    const user = userEvent.setup();
    mockApi({ applyBody: { outcomes: [{ file_id: 1 }],
                           errors: [{ file_id: 2, error: "tracks changed" }] } });
    show();

    await answerCard(user);
    await user.click(applyButton());

    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(toast).toHaveBeenCalledWith("1 file answered, 1 could not be", "warning");
  });

  it("says so when the whole call fails", async () => {
    const user = userEvent.setup();
    mockApi({ applyOk: false, applyBody: { detail: "nope" } });
    show();

    await answerCard(user);
    await user.click(applyButton());

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith("Could not apply these decisions", "error"));
  });
});

describe("the outcome line", () => {
  it("shows what the server worked out for the staged answers", async () => {
    const user = userEvent.setup();
    mockApi();
    show();

    await answerCard(user);

    expect(await screen.findByText("Converts to MP4, 2 deleted")).toBeInTheDocument();
    const preview = posted.find(p => p.url.includes("/review/preview"));
    expect(preview.body.files).toEqual([
      { file_id: 1, answers: { 2: "remove", 3: "remove" } },
      { file_id: 2, answers: { 4: "remove", 5: "remove" } },
    ]);
  });

  it("says what the server said, not what the answers suggest", async () => {
    const user = userEvent.setup();
    // Deleting both tracks looks like a conversion, and something else about
    // these files - their audio, or the container they are already in -
    // means it is not. Only the engine knows that.
    mockApi({ previewContainer: "mkv" });
    show();

    await answerCard(user);

    expect(await screen.findByText("Stays MKV, 2 deleted")).toBeInTheDocument();
  });

  it("takes the line down when a later preview fails", async () => {
    const user = userEvent.setup();
    mockApi();
    show();

    await answerCard(user);
    expect(await screen.findByText("Converts to MP4, 2 deleted")).toBeInTheDocument();

    // The answers change and the server cannot be reached. The line on screen
    // describes the answers from before: leaving it up is a sentence about a
    // decision nobody is making any more.
    const previous = global.fetch;
    global.fetch = vi.fn(async (url, opts) =>
      String(url).includes("/review/preview")
        ? Promise.reject(new Error("network"))
        : previous(url, opts));
    await user.click(screen.getAllByRole("button", { name: "Keep" })[0]);

    await waitFor(() =>
      expect(screen.queryByText("Converts to MP4, 2 deleted")).toBeNull());
  });

  it("offers the conversion only when the kept tracks are what hold the file", async () => {
    const user = userEvent.setup();
    mockApi({ previewContainer: "mkv" });
    show();

    await screen.findByText("Show / Season 1");
    // Keep the styled track, delete the bitmap one.
    await user.click(screen.getAllByRole("button", { name: "Keep" })[0]);
    await user.click(screen.getAllByRole("button", { name: "Delete" })[1]);

    expect(await screen.findByText(
      "Stays MKV, 1 kept, 1 deleted — delete the kept tracks and it converts to MP4",
    )).toBeInTheDocument();
  });

  it("says so when the file stays MKV whatever is chosen", async () => {
    const user = userEvent.setup();
    // Its audio or video is what holds it, so no answer on this card moves it.
    mockApi({ previewContainer: "mkv", blockedBeyondSubtitles: true });
    show();

    await screen.findByText("Show / Season 1");
    await user.click(screen.getAllByRole("button", { name: "Keep" })[0]);
    await user.click(screen.getAllByRole("button", { name: "Delete" })[1]);

    expect(await screen.findByText(
      "Stays MKV, 1 kept, 1 deleted — it stays MKV whatever you choose here",
    )).toBeInTheDocument();
  });

  it("promises no conversion when nothing is kept and it still stays MKV", async () => {
    const user = userEvent.setup();
    mockApi({ previewContainer: "mkv" });
    show();

    await answerCard(user);

    // Deleting everything and still not converting means the reason is not on
    // this card, so there is nothing to offer deleting.
    expect(await screen.findByText("Stays MKV, 2 deleted")).toBeInTheDocument();
  });

  it("does not call an MP4 that stays an MP4 a conversion", async () => {
    const user = userEvent.setup();
    mockApi({ currentContainer: "mp4", previewContainer: "mp4" });
    show();

    await answerCard(user);

    expect(await screen.findByText("Stays MP4, 2 deleted")).toBeInTheDocument();
  });

  it("counts a file that is already MP4 as staying, not converting", async () => {
    const user = userEvent.setup();
    mockApi({ previewPerFile: {
      1: { current_container: "mp4", target_container: "mp4" },
      2: { current_container: "mkv", target_container: "mp4" },
    } });
    show();

    await answerCard(user);

    expect(await screen.findByText(
      "Converts 1 of 2 to MP4, 2 deleted. The other 1 stay as they are.",
    )).toBeInTheDocument();
  });

  it("says which of a mixed card's files cannot move whatever is chosen", async () => {
    const user = userEvent.setup();
    mockApi({ previewPerFile: {
      1: { current_container: "mkv", target_container: "mkv",
           blocked_beyond_subtitles: true },
      2: { current_container: "mkv", target_container: "mp4" },
    } });
    show();

    await answerCard(user);

    expect(await screen.findByText(
      "Converts 1 of 2 to MP4, 2 deleted. The other 1 stay as they are "
      + "whatever you choose here.",
    )).toBeInTheDocument();
  });

  it("shows no line at all when the preview cannot be had", async () => {
    const user = userEvent.setup();
    mockApi();
    const realFetch = global.fetch;
    global.fetch = vi.fn(async (url, opts) =>
      String(url).includes("/review/preview")
        ? Promise.reject(new Error("network"))
        : realFetch(url, opts));
    show();

    await answerCard(user);

    await waitFor(() => expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/review/preview"), expect.anything()));
    expect(screen.queryByText(/Converts/)).toBeNull();
    expect(screen.queryByText(/Stays MKV/)).toBeNull();
  });
});

/**
 * Paging when the list moves between two loads.
 *
 * Cards arrive a page at a time, and the list can change in between: a card
 * answered in another tab shifts everything after it, and /review/apply
 * broadcasts nothing, so this page is not told. The next page can then open
 * on a card already shown.
 *
 * A card's key is its identity now (the server's folder and flagged tracks),
 * so a repeat is the same question and is dropped — shown twice, its files
 * went twice on Apply. Dropping it means the cards on screen no longer count
 * the server's offset, so paging advances by what each response carried.
 *
 * Each mutation below survived the whole suite before these existed:
 *
 *   • a repeated card appended again
 *   • the next page asked for at the number of cards on screen, which asks
 *     for the same offset forever once one is dropped
 *   • "more to load" judged by the cards on screen, which never reaches the
 *     total once one is dropped, and keeps asking past the end
 */
describe("paging while the list moves", () => {
  /* The sentinel is in view: every observe() reports it immediately, the way
   * a real IntersectionObserver does for a target already on screen. */
  class VisibleObserver {
    constructor(callback) { this.callback = callback; }
    observe() { this.callback([{ isIntersecting: true }]); }
    disconnect() {}
  }

  const A = { ...GROUP, key: "card-a" };
  const C = { ...SECOND, key: "card-c" };

  /* Three cards in all. The page at offset 1 opens on A again, as it would
   * after a card ahead of it was answered elsewhere. */
  function mockPages(pages = { 0: [A], 1: [A], 2: [C] }, total = 3) {
    const offsets = [];
    posted = [];
    toast = vi.fn();
    global.fetch = vi.fn(async (url, opts = {}) => {
      const u = String(url);
      if (u.includes("/review/groups")) {
        const offset = Number(new URL(u).searchParams.get("offset"));
        offsets.push(offset);
        return { ok: true, json: async () => ({
          groups: pages[offset] || [], total_groups: total, total_files: total,
        }) };
      }
      if (opts.method === "POST") {
        const body = JSON.parse(opts.body);
        posted.push({ url: u, body });
        return { ok: true, json: async () => ({
          outcomes: (body.files || []).map(f => ({ file_id: f.file_id })),
          errors: [],
        }) };
      }
      return { ok: true, json: async () => ({ items: [], total: 0, files: [] }) };
    });
    return offsets;
  }

  /* Long enough for a runaway loader to ask several more times. */
  const settle = () => new Promise(resolve => setTimeout(resolve, 50));

  it("shows a repeated card once and stops at the end of the list", async () => {
    vi.stubGlobal("IntersectionObserver", VisibleObserver);
    const offsets = mockPages();
    show();

    await screen.findByText(C.heading);
    await settle();

    expect(screen.getAllByText(A.heading)).toHaveLength(1);
    expect(offsets).toEqual([0, 1, 2]);
  });

  it("sends a repeated card's files once", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("IntersectionObserver", VisibleObserver);
    mockPages();
    show();

    await screen.findByText(C.heading);
    await answerCard(user);
    await user.click(applyButton());

    await waitFor(() => expect(applyRequest()).toBeDefined());
    expect(applyRequest().files.map(f => f.file_id)).toEqual([1, 2]);
  });
});
