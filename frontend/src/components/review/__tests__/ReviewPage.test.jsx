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
 *
 * This list used to end with "staged answers surviving a refresh". Nothing
 * here tested that: deleting the reset left the whole suite green. The
 * behaviour has since been reversed on purpose, and what now pins it is the
 * refresh block at the end of this file.
 *
 * And, added later, each of these survived the suite of its day:
 *
 *   • every outcome called an answer, skips included
 *   • a skip counted from the request, so one the server refused was
 *     still called skipped
 *   • the skipped count dropped when some files were also answered
 *   • a request of only skips summarised as "0 files answered"
 *
 * Those four lived because the apply mocks answered body.files alone. The
 * endpoint returns an outcome for every skip as well, with the same fields
 * as an answer — which is the whole of the bug — so a mock without them
 * could not show it. Both mocks below now answer skips the way it does.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
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
        /* As /review/apply answers: one outcome per file, skipped ones
         * included and indistinguishable by their fields. */
        json: async () => applyBody
          ?? { outcomes: [
                 ...JSON.parse(opts.body).files.map(f => ({ file_id: f.file_id })),
                 ...JSON.parse(opts.body).skips.map(id => ({ file_id: id })),
               ],
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

  it("calls skipped files skipped, not answered", async () => {
    const user = userEvent.setup();
    mockApi({ groups: [GROUP] });
    show();

    await screen.findByText("Show / Season 1");
    await user.click(screen.getByRole("button", { name: "Skip" }));
    expect(applyButton()).toHaveTextContent("Apply: 0 decided, 2 skipped");
    await user.click(applyButton());

    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(toast).toHaveBeenCalledWith("2 files skipped", "success");
  });

  it("names answered and skipped files separately", async () => {
    const user = userEvent.setup();
    mockApi({ groups: [GROUP, SECOND] });
    show();

    await answerCard(user);
    await user.click(screen.getAllByRole("button", { name: "Skip" })[1]);
    await user.click(applyButton());

    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(toast).toHaveBeenCalledWith("2 files answered, 1 skipped", "success");
  });

  it("does not call a skip the server refused skipped", async () => {
    // The file was answered or rescanned elsewhere first. What the server
    // did is one skip and one refusal, not two skips.
    const user = userEvent.setup();
    mockApi({ groups: [GROUP],
              applyBody: { outcomes: [{ file_id: 1 }],
                           errors: [{ file_id: 2, error: "No file waiting in review" }] } });
    show();

    await screen.findByText("Show / Season 1");
    await user.click(screen.getByRole("button", { name: "Skip" }));
    await user.click(applyButton());

    await waitFor(() => expect(toast).toHaveBeenCalled());
    expect(toast).toHaveBeenCalledWith("1 file skipped, 1 could not be", "warning");
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
          outcomes: [
            ...(body.files || []).map(f => ({ file_id: f.file_id })),
            ...(body.skips || []).map(id => ({ file_id: id })),
          ],
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

/**
 * A refresh while choices are staged.
 *
 * reviewRefreshKey is bumped whenever a job finishes or a file is queued, so
 * on a busy queue it moves every minute or so while someone is working
 * through the cards. Staging used to be dropped on every bump, which erased
 * every choice not yet applied. It is kept now, and these pin what keeping it
 * means: the choice follows the card by its key, the stream numbers come from
 * the card as last loaded, and a card that did not come back is not sent.
 *
 * Each mutation below survived the whole suite before these existed:
 *
 *   • staging dropped on a refresh again
 *   • a first-page load appended to the old list instead of replacing it,
 *     which keeps the card as it was before the refresh — old stream
 *     numbers, old files, and cards that have gone
 *   • staging kept after Apply
 */
describe("a refresh while choices are staged", () => {
  /* The page as it is at each load: the nth request for the first page gets
   * loads[n], and the last entry repeats. */
  function mockLoads(loads, { applyBody = null } = {}) {
    posted = [];
    toast = vi.fn();
    let n = 0;
    global.fetch = vi.fn(async (url, opts = {}) => {
      const u = String(url);
      if (u.includes("/review/groups")) {
        const groups = loads[Math.min(n++, loads.length - 1)];
        return { ok: true, json: async () => ({
          groups, total_groups: groups.length,
          total_files: groups.reduce((t, g) => t + g.file_count, 0),
        }) };
      }
      if (opts.method === "POST") {
        const body = JSON.parse(opts.body);
        posted.push({ url: u, body });
        if (u.includes("/review/preview")) {
          return { ok: true, json: async () => ({ outcomes: [], errors: [] }) };
        }
        return { ok: true, json: async () => applyBody ?? {
          outcomes: [
            ...body.files.map(f => ({ file_id: f.file_id })),
            ...body.skips.map(id => ({ file_id: id })),
          ],
          errors: [],
        } };
      }
      return { ok: true, json: async () => ({ items: [], total: 0, files: [] }) };
    });
    return () => n;
  }

  const page = key =>
    <ReviewPage api={API} toast={toast} onReviewResolved={() => {}} reviewRefreshKey={key} />;

  /* Bump the key the way a finished job does, and wait for the reload to
   * land rather than for the old page to still be on screen. */
  async function refresh(rerender, loadsSoFar, heading) {
    const before = loadsSoFar();
    rerender(page(1));
    await waitFor(() => expect(loadsSoFar()).toBe(before + 1));
    await screen.findByText(heading);
  }

  it("keeps a choice when the same card comes back", async () => {
    const user = userEvent.setup();
    const loads = mockLoads([[GROUP]]);
    const { rerender } = render(page(0));

    await answerCard(user);
    await refresh(rerender, loads, GROUP.heading);

    expect(applyButton()).toHaveTextContent("Apply: 2 decided");
    await user.click(applyButton());
    await waitFor(() => expect(applyRequest()).toBeDefined());
    expect(applyRequest().files).toEqual([
      { file_id: 1, answers: { 2: "remove", 3: "remove" } },
      { file_id: 2, answers: { 4: "remove", 5: "remove" } },
    ]);
  });

  it("sends a file re-probed since the choice its new stream numbers", async () => {
    const user = userEvent.setup();
    const reprobed = { ...GROUP, files: [
      GROUP.files[0],
      { ...GROUP.files[1], streams: [6, 7] },
    ] };
    const loads = mockLoads([[GROUP], [reprobed]]);
    const { rerender } = render(page(0));

    await answerCard(user);
    await refresh(rerender, loads, GROUP.heading);
    await user.click(applyButton());

    await waitFor(() => expect(applyRequest()).toBeDefined());
    expect(applyRequest().files).toEqual([
      { file_id: 1, answers: { 2: "remove", 3: "remove" } },
      { file_id: 2, answers: { 6: "remove", 7: "remove" } },
    ]);
  });

  it("neither counts nor sends a card that did not come back", async () => {
    // Both cards answered; the first was then answered in another tab.
    const user = userEvent.setup();
    const loads = mockLoads([[GROUP, SECOND], [SECOND]]);
    const { rerender } = render(page(0));

    await answerCard(user);
    const deletes = () => screen.getAllByRole("button", { name: "Delete" });
    await user.click(deletes()[2]);
    await user.click(deletes()[3]);
    expect(applyButton()).toHaveTextContent("Apply: 3 decided");

    await refresh(rerender, loads, SECOND.heading);
    await waitFor(() => expect(screen.queryByText(GROUP.heading)).toBeNull());

    expect(applyButton()).toHaveTextContent("Apply: 1 decided");
    await user.click(applyButton());
    await waitFor(() => expect(applyRequest()).toBeDefined());
    expect(applyRequest().files.map(f => f.file_id)).toEqual([3]);
  });

  it("gives a file that joined the card since the choice the card's answer", async () => {
    // A new episode with the same tracks in the same folder is the same
    // question, so it is answered with the card. Its own stream numbers.
    const user = userEvent.setup();
    const joined = { ...GROUP, file_count: 3, files: [
      ...GROUP.files,
      { file_id: 4, filename: "ep03.mkv", path: "/media/tv/Show/Season 1/ep03.mkv",
        streams: [8, 9] },
    ] };
    const loads = mockLoads([[GROUP], [joined]]);
    const { rerender } = render(page(0));

    await answerCard(user);
    await refresh(rerender, loads, GROUP.heading);
    await waitFor(() => expect(applyButton()).toHaveTextContent("Apply: 3 decided"));

    await user.click(applyButton());
    await waitFor(() => expect(applyRequest()).toBeDefined());
    expect(applyRequest().files).toContainEqual(
      { file_id: 4, answers: { 8: "remove", 9: "remove" } });
  });

  it("clears what it sent once Apply is done", async () => {
    // The server refused both files, so the card is still waiting when the
    // page reloads. It comes back unanswered: the choice was made and sent,
    // and carrying it into the next Apply would send it twice.
    const user = userEvent.setup();
    mockLoads([[GROUP]], { applyBody: { outcomes: [], errors: [
      { file_id: 1, error: "tracks changed" }, { file_id: 2, error: "tracks changed" },
    ] } });
    render(page(0));

    await answerCard(user);
    await user.click(applyButton());

    await waitFor(() => expect(toast).toHaveBeenCalled());
    await screen.findByText(GROUP.heading);
    expect(screen.queryByRole("button", { name: /^Apply/ })).toBeNull();
  });
});

/**
 * Which cards are previewed.
 *
 * A card's outcome line is worked out by the engine, one run per file, so
 * the page asks only when the line could have changed: the card's answers
 * are not the ones last sent, or its files are not. Its files means which
 * ones, their stream numbers, and each file's size and mtime — a file
 * replaced at the same path keeps its card and its streams, and only those
 * two say it is not the file that was previewed.
 *
 * Each mutation below survived the whole suite before these existed:
 *
 *   • every decided card previewed on every change
 *   • what was sent recorded when the effect ran rather than when the
 *     request went out
 *   • a card's files left out of deciding whether to preview it
 *   • every line dropped on a refresh
 *   • a failed preview remembered as sent, so never asked again
 *   • size, or mtime, left out of what counts as the card's files
 *   • the line of a card whose files changed left up until the new one came
 */
describe("which cards are previewed", () => {
  const LINE = "Converts to MP4, 2 deleted";

  /* The first-page loads in order (the last repeats), and every preview
   * request recorded as the file ids it carried. mode: "ok" answers,
   * "fail" rejects, "hang" never answers. */
  function mockPreviews(loads, { mode = "ok" } = {}) {
    posted = [];
    toast = vi.fn();
    const state = { mode, previews: [], loads: 0 };
    global.fetch = vi.fn(async (url, opts = {}) => {
      const u = String(url);
      if (u.includes("/review/groups")) {
        /* A fresh copy per load, as parsed JSON is. Handing back the same
         * objects lets React skip the update on a refresh, and a test of
         * what a refresh re-previews then passes without one happening. */
        const groups = structuredClone(loads[Math.min(state.loads++, loads.length - 1)]);
        return { ok: true, json: async () => ({
          groups, total_groups: groups.length,
          total_files: groups.reduce((t, g) => t + g.file_count, 0),
        }) };
      }
      if (u.includes("/review/preview")) {
        const body = JSON.parse(opts.body);
        state.previews.push(body.files.map(f => f.file_id));
        if (state.mode === "fail") throw new Error("network");
        if (state.mode === "hang") return new Promise(() => {});
        return { ok: true, json: async () => ({
          outcomes: body.files.map(f => ({
            file_id: f.file_id, current_container: "mkv", target_container: "mp4",
            will_process: true, still_in_review: false, blocked_beyond_subtitles: false,
          })),
          errors: [],
        }) };
      }
      return { ok: true, json: async () => ({ items: [], total: 0, files: [] }) };
    });
    return state;
  }

  const page = key =>
    <ReviewPage api={API} toast={toast} onReviewResolved={() => {}} reviewRefreshKey={key} />;

  /* Longer than the debounce, so anything the page was going to send has
   * gone by the time this returns. Inside act, because the previews it lets
   * through update the page. */
  const quiet = () => act(() => new Promise(resolve => setTimeout(resolve, 400)));

  const deletes = () => screen.getAllByRole("button", { name: "Delete" });

  async function refresh(rerender, state, heading) {
    const before = state.loads;
    rerender(page(1));
    await waitFor(() => expect(state.loads).toBe(before + 1));
    await screen.findByText(heading);
  }

  it("previews only the card a click changed", async () => {
    const user = userEvent.setup();
    const state = mockPreviews([[GROUP, SECOND]]);
    render(page(0));

    await answerCard(user);
    await waitFor(() => expect(state.previews).toEqual([[1, 2]]));
    await user.click(deletes()[2]);
    await user.click(deletes()[3]);
    await waitFor(() => expect(state.previews).toEqual([[1, 2], [3]]));

    await user.click(screen.getAllByRole("button", { name: "Keep" })[2]);
    await quiet();
    expect(state.previews).toEqual([[1, 2], [3], [3]]);
  });

  it("previews both of two cards answered inside the debounce", async () => {
    const user = userEvent.setup();
    const state = mockPreviews([[GROUP, SECOND]]);
    render(page(0));

    await screen.findByText(SECOND.heading);
    await user.click(deletes()[0]);
    await user.click(deletes()[2]);
    // Each click below completes a card, the second well inside 250ms.
    await user.click(deletes()[1]);
    await user.click(deletes()[3]);
    await quiet();

    expect(state.previews).toContainEqual([1, 2]);
    expect(state.previews).toContainEqual([3]);
  });

  it("sends nothing and keeps the line when a refresh brings the same cards", async () => {
    const user = userEvent.setup();
    const state = mockPreviews([[GROUP]]);
    const { rerender } = render(page(0));

    await answerCard(user);
    await screen.findByText(LINE);
    await refresh(rerender, state, GROUP.heading);
    await quiet();

    expect(state.previews).toEqual([[1, 2]]);
    expect(screen.getByText(LINE)).toBeInTheDocument();
  });

  it("previews a card again with a file that joined it", async () => {
    const user = userEvent.setup();
    const joined = { ...GROUP, file_count: 3, files: [
      ...GROUP.files,
      { file_id: 4, filename: "ep03.mkv", path: "/media/tv/Show/Season 1/ep03.mkv",
        streams: [8, 9] },
    ] };
    const state = mockPreviews([[GROUP], [joined]]);
    const { rerender } = render(page(0));

    await answerCard(user);
    await waitFor(() => expect(state.previews).toEqual([[1, 2]]));
    await refresh(rerender, state, GROUP.heading);

    await waitFor(() => expect(state.previews).toEqual([[1, 2], [1, 2, 4]]));
  });

  it("takes a card's line down as soon as its files change", async () => {
    // The new preview never arrives: the old line is about files the card
    // no longer has, so it goes without waiting for its replacement.
    const user = userEvent.setup();
    const joined = { ...GROUP, file_count: 3, files: [
      ...GROUP.files,
      { file_id: 4, filename: "ep03.mkv", path: "/x/ep03.mkv", streams: [8, 9] },
    ] };
    const state = mockPreviews([[GROUP], [joined]]);
    const { rerender } = render(page(0));

    await answerCard(user);
    await screen.findByText(LINE);
    state.mode = "hang";
    await refresh(rerender, state, GROUP.heading);

    await waitFor(() => expect(screen.queryByText(LINE)).toBeNull());
  });

  it("asks again, on the next change, for a card whose preview failed", async () => {
    const user = userEvent.setup();
    const state = mockPreviews([[GROUP, SECOND]], { mode: "fail" });
    render(page(0));

    await answerCard(user);
    await waitFor(() => expect(state.previews).toEqual([[1, 2]]));
    state.mode = "ok";
    await user.click(deletes()[2]);
    await user.click(deletes()[3]);

    await waitFor(() => expect(state.previews.slice(1)).toContainEqual([1, 2]));
    // Both cards now have their line, the one that failed included.
    await waitFor(() => expect(screen.getAllByText(LINE)).toHaveLength(2));
  });

  it.each([
    ["size", { size: 5_000_000 }],
    ["mtime", { mtime: 1_700_000_000 }],
  ])("previews a card again when a file's %s changed under the same streams", async (_what, change) => {
    // Replaced at the same path with the same subtitle layout: same card,
    // same stream numbers, different file.
    const user = userEvent.setup();
    const replaced = { ...GROUP, files: [
      GROUP.files[0], { ...GROUP.files[1], ...change },
    ] };
    const state = mockPreviews([[GROUP], [replaced]]);
    const { rerender } = render(page(0));

    await answerCard(user);
    await waitFor(() => expect(state.previews).toEqual([[1, 2]]));
    await refresh(rerender, state, GROUP.heading);

    await waitFor(() => expect(state.previews).toEqual([[1, 2], [1, 2]]));
  });
});

/**
 * Previews that land late.
 *
 * A response can outlive its question. It is used only if its card has not
 * changed since it was sent: not re-answered, not given new files, not
 * applied. A card whose answers change still keeps its current line until
 * the new one arrives — what goes is a response that is out of date by the
 * time it lands.
 *
 * Each mutation below survived the whole suite before these existed:
 *
 *   • a late response used without checking its card
 *   • a late failure used without checking its card
 *   • a card's last preview still counted as current after the card changed
 *   • records kept through Apply
 *   • a card changed mid-batch still sent
 */
describe("previews that land late", () => {
  const DELETED = "Converts to MP4, 2 deleted";
  const STAYS = /^Stays MKV/;

  /* Every preview waits until the test releases it: resolve(target) answers
   * with that container for every file, fail() rejects. Arrival order is
   * then whatever the test says, not whatever the timers make it. */
  function mockHeld(loads, { applyBody } = {}) {
    posted = [];
    toast = vi.fn();
    const state = { requests: [], loads: 0 };
    global.fetch = vi.fn(async (url, opts = {}) => {
      const u = String(url);
      if (u.includes("/review/groups")) {
        const groups = structuredClone(loads[Math.min(state.loads++, loads.length - 1)]);
        return { ok: true, json: async () => ({
          groups, total_groups: groups.length,
          total_files: groups.reduce((t, g) => t + g.file_count, 0),
        }) };
      }
      const body = JSON.parse(opts.body);
      if (u.includes("/review/preview")) {
        return new Promise((resolve, reject) => state.requests.push({
          ids: body.files.map(f => f.file_id),
          choices: Object.values(body.files[0].answers),
          resolve: target => resolve({ ok: true, json: async () => ({
            outcomes: body.files.map(f => ({
              file_id: f.file_id, current_container: "mkv", target_container: target,
              will_process: true, still_in_review: false, blocked_beyond_subtitles: false,
            })),
            errors: [],
          }) }),
          fail: () => reject(new Error("network")),
        }));
      }
      posted.push({ url: u, body });
      return { ok: true, json: async () => applyBody };
    });
    return state;
  }

  const page = key =>
    <ReviewPage api={API} toast={toast} onReviewResolved={() => {}} reviewRefreshKey={key} />;
  const release = fn => act(async () => { fn(); });
  const quiet = () => act(() => new Promise(resolve => setTimeout(resolve, 400)));
  const buttons = name => screen.getAllByRole("button", { name });

  async function answerBoth(user, choice, from = 0) {
    await user.click(buttons(choice)[from]);
    await user.click(buttons(choice)[from + 1]);
  }

  it("does not let a slow preview for answers since changed replace the newer line", async () => {
    const user = userEvent.setup();
    const state = mockHeld([[GROUP]]);
    render(page(0));
    await screen.findByText(GROUP.heading);

    await answerBoth(user, "Keep");
    await waitFor(() => expect(state.requests).toHaveLength(1));
    await answerBoth(user, "Delete");
    await waitFor(() => expect(state.requests).toHaveLength(2));

    await release(() => state.requests[1].resolve("mp4"));
    await screen.findByText(DELETED);
    await release(() => state.requests[0].resolve("mkv"));

    expect(screen.getByText(DELETED)).toBeInTheDocument();
    expect(screen.queryByText(STAYS)).toBeNull();
  });

  it("does not let a slow failure for answers since changed take the newer line down", async () => {
    const user = userEvent.setup();
    const state = mockHeld([[GROUP]]);
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(page(0));
    await screen.findByText(GROUP.heading);

    await answerBoth(user, "Keep");
    await waitFor(() => expect(state.requests).toHaveLength(1));
    await answerBoth(user, "Delete");
    await waitFor(() => expect(state.requests).toHaveLength(2));

    await release(() => state.requests[1].resolve("mp4"));
    await screen.findByText(DELETED);
    await release(() => state.requests[0].fail());

    expect(screen.getByText(DELETED)).toBeInTheDocument();
    console.error.mockRestore();
  });

  it("drops a preview for answers since changed that lands before the new one is sent", async () => {
    // Inside the debounce the new request has not gone out yet, so nothing
    // has replaced the old one's record except the change itself.
    const user = userEvent.setup();
    const state = mockHeld([[GROUP]]);
    render(page(0));
    await screen.findByText(GROUP.heading);

    await answerBoth(user, "Keep");
    await waitFor(() => expect(state.requests).toHaveLength(1));
    await answerBoth(user, "Delete");
    await release(() => state.requests[0].resolve("mkv"));
    await quiet();

    expect(state.requests).toHaveLength(2);   // the new one went out, still held
    expect(screen.queryByText(STAYS)).toBeNull();
  });

  it("does not put back the line of a card whose files changed", async () => {
    // A preview for the card's old files is in flight when a refresh brings
    // a new file. The line comes down at once and stays down.
    const user = userEvent.setup();
    const joined = { ...GROUP, file_count: 3, files: [
      ...GROUP.files,
      { file_id: 4, filename: "ep03.mkv", path: "/x/ep03.mkv", streams: [8, 9] },
    ] };
    const state = mockHeld([[GROUP], [joined]]);
    const { rerender } = render(page(0));
    await screen.findByText(GROUP.heading);

    await answerBoth(user, "Delete");
    await waitFor(() => expect(state.requests).toHaveLength(1));
    await release(() => state.requests[0].resolve("mp4"));
    await screen.findByText(DELETED);
    await user.click(buttons("Keep")[0]);
    await waitFor(() => expect(state.requests).toHaveLength(2));   // old files, held

    rerender(page(1));
    await waitFor(() => expect(screen.queryByText(DELETED)).toBeNull());
    await release(() => state.requests[1].resolve("mkv"));
    await quiet();

    expect(state.requests.at(-1).ids).toEqual([1, 2, 4]);         // new files, held
    expect(screen.queryByText(STAYS)).toBeNull();
  });

  it("does not put a line under a card left with no answers by Apply", async () => {
    // The server refused both files, so the card is still on the page after
    // Apply, unanswered. The preview sent before Apply then lands.
    const user = userEvent.setup();
    const state = mockHeld([[GROUP]], { applyBody: { outcomes: [], errors: [
      { file_id: 1, error: "tracks changed" }, { file_id: 2, error: "tracks changed" },
    ] } });
    render(page(0));
    await screen.findByText(GROUP.heading);

    await answerBoth(user, "Delete");
    await waitFor(() => expect(state.requests).toHaveLength(1));
    await user.click(applyButton());
    await waitFor(() => expect(toast).toHaveBeenCalled());
    await waitFor(() => expect(state.loads).toBe(2));
    await release(() => state.requests[0].resolve("mp4"));

    expect(screen.queryByText(DELETED)).toBeNull();
  });

  it("does not send a card that changed while earlier ones in its batch were waiting", async () => {
    // Both cards answered inside one debounce: one batch, sent in turn. The
    // second card is changed while the first card's preview is held.
    const user = userEvent.setup();
    const state = mockHeld([[GROUP, SECOND]]);
    render(page(0));
    await screen.findByText(SECOND.heading);

    await user.click(buttons("Delete")[0]);
    await user.click(buttons("Delete")[2]);
    await user.click(buttons("Delete")[1]);
    await user.click(buttons("Delete")[3]);
    await waitFor(() => expect(state.requests).toHaveLength(1));
    expect(state.requests[0].ids).toEqual([1, 2]);

    await user.click(buttons("Keep")[2]);                          // second card changes
    await waitFor(() => expect(state.requests).toHaveLength(2));
    await release(() => state.requests[0].resolve("mp4"));
    await quiet();

    const second = state.requests.filter(r => r.ids[0] === 3).map(r => r.choices);
    expect(second).toEqual([["keep", "remove"]]);
  });
});

/**
 * Page loads that a refresh has overtaken.
 *
 * A refresh starts a new list. Anything already in flight for the old one —
 * a later page the sentinel asked for, or an earlier refresh's first page —
 * is dropped when it lands: its cards, its offset, its totals, and its hold
 * on the loading flag.
 *
 * Each mutation below survived the whole suite before these existed:
 *
 *   • a load that a newer list overtook used anyway
 *   • a superseded load clearing the loading flag under the newer one
 *   • only later pages checked, so an older first page still replaced a
 *     newer one
 */
describe("page loads a refresh has overtaken", () => {
  class VisibleObserver {
    constructor(callback) { this.callback = callback; }
    observe() { this.callback([{ isIntersecting: true }]); }
    disconnect() {}
  }

  const THIRD = { ...SECOND, key: "g3", heading: "Third / Season 3",
                  files: [{ file_id: 5, filename: "y.mkv", path: "/y.mkv", streams: [2, 3] }] };

  /* Every request for cards waits until the test answers it, so which load
   * lands first is the test's choice. */
  function mockHeldPages() {
    const requests = [];
    global.fetch = vi.fn((url) => {
      const u = String(url);
      if (u.includes("/review/groups")) {
        const offset = Number(new URL(u).searchParams.get("offset"));
        return new Promise(resolve => requests.push({
          offset,
          answer: (groups, total) => resolve({ ok: true, json: async () => ({
            groups: structuredClone(groups), total_groups: total,
            total_files: groups.reduce((t, g) => t + g.file_count, 0),
          }) }),
        }));
      }
      return Promise.resolve({ ok: true, json: async () => ({ items: [], total: 0, files: [] }) });
    });
    return requests;
  }

  const page = key => <ReviewPage api={API} reviewRefreshKey={key} />;
  const answer = (request, groups, total) => act(async () => { request.answer(groups, total); });
  const quiet = () => act(() => new Promise(resolve => setTimeout(resolve, 100)));

  it("does not add a page asked for before a refresh to the list after it", async () => {
    // The second page is in flight when a refresh lands. By then both cards
    // on the old list were answered elsewhere; only SECOND is waiting.
    vi.stubGlobal("IntersectionObserver", VisibleObserver);
    const requests = mockHeldPages();
    const { rerender } = render(page(0));

    await waitFor(() => expect(requests).toHaveLength(1));
    await answer(requests[0], [GROUP], 2);
    await waitFor(() => expect(requests).toHaveLength(2));          // offset 1, held
    expect(requests[1].offset).toBe(1);

    rerender(page(1));
    await waitFor(() => expect(requests).toHaveLength(3));          // offset 0, new list
    await answer(requests[2], [SECOND], 1);
    await screen.findByText(SECOND.heading);
    await answer(requests[1], [THIRD], 2);
    await quiet();

    expect(screen.queryByText(THIRD.heading)).toBeNull();
    expect(screen.queryByText(GROUP.heading)).toBeNull();
  });

  it("does not ask for more of the old list while the new one is loading", async () => {
    // The overtaken page lands first. The new first page is still loading,
    // so the sentinel must not take that as its cue to ask for the next page.
    vi.stubGlobal("IntersectionObserver", VisibleObserver);
    const requests = mockHeldPages();
    const { rerender } = render(page(0));

    await waitFor(() => expect(requests).toHaveLength(1));
    await answer(requests[0], [GROUP], 2);
    await waitFor(() => expect(requests).toHaveLength(2));          // offset 1, held

    rerender(page(1));
    await waitFor(() => expect(requests).toHaveLength(3));          // offset 0, held
    await answer(requests[1], [THIRD], 2);
    await quiet();

    expect(requests.map(r => r.offset)).toEqual([0, 1, 0]);
  });

  it("keeps the newer of two refreshes when the older lands last", async () => {
    const requests = mockHeldPages();
    const { rerender } = render(page(0));

    await waitFor(() => expect(requests).toHaveLength(1));
    await answer(requests[0], [GROUP], 1);
    await screen.findByText(GROUP.heading);

    rerender(page(1));
    await waitFor(() => expect(requests).toHaveLength(2));
    rerender(page(2));
    await waitFor(() => expect(requests).toHaveLength(3));
    await answer(requests[2], [THIRD], 1);
    await screen.findByText(THIRD.heading);
    await answer(requests[1], [SECOND], 1);
    await quiet();

    expect(screen.getByText(THIRD.heading)).toBeInTheDocument();
    expect(screen.queryByText(SECOND.heading)).toBeNull();
  });
});

/**
 * Answering file by file.
 *
 * A card is decided once every file has an answer for every track, its own
 * or the card's. Before, only the card's own row counted, so a card answered
 * entirely file by file showed every choice made and sent nothing.
 *
 * Mutation, 3 applied against the unchanged suite. One was already killed,
 * by "sends a file's own answer only for that file" above, and is recorded
 * here as covered rather than claimed below:
 *
 *   • only the card's row counted, as before         → killed below
 *   • any complete file enough, rather than every    → killed below
 *   • a file with answers of its own ignoring the
 *     card's for the tracks it has not set           → already covered
 */
describe("answering file by file", () => {
  const button = name => screen.getAllByRole("button", { name });

  /* Opens one file's own row and sets both its tracks. A file's buttons
   * follow the card's, so they are the last two of each name. */
  async function answerFile(user, filename, first, second) {
    await user.click(screen.getByRole("button", { name: new RegExp(filename) }));
    await user.click(button(first).at(-2));
    await user.click(button(second).at(-1));
  }

  async function showFiles(user) {
    await screen.findByText(GROUP.heading);
    await user.click(screen.getByRole("button", { name: /Show files/ }));
  }

  it("counts a card whose every file was answered on its own", async () => {
    const user = userEvent.setup();
    mockApi();
    show();
    await showFiles(user);

    await answerFile(user, "ep01.mkv", "Delete", "Keep");
    await answerFile(user, "ep02.mkv", "Keep", "Delete");
    expect(applyButton()).toHaveTextContent("Apply: 2 decided");
    await user.click(applyButton());

    await waitFor(() => expect(applyRequest()).toBeTruthy());
    expect(applyRequest().files).toEqual([
      { file_id: 1, answers: { 2: "remove", 3: "keep" } },
      { file_id: 2, answers: { 4: "keep", 5: "remove" } },
    ]);
  });

  it("does not count it while any file is still unanswered", async () => {
    const user = userEvent.setup();
    mockApi();
    show();
    await showFiles(user);

    await answerFile(user, "ep01.mkv", "Delete", "Keep");

    expect(screen.queryByRole("button", { name: /^Apply/ })).toBeNull();
  });

  it("counts a track answered on the card and the rest file by file", async () => {
    // The card answers the first track for every file; each file answers
    // the second on its own.
    const user = userEvent.setup();
    mockApi();
    show();
    await showFiles(user);

    await user.click(button("Delete")[0]);
    await user.click(screen.getByRole("button", { name: /ep01.mkv/ }));
    await user.click(button("Keep").at(-1));
    await user.click(screen.getByRole("button", { name: /ep02.mkv/ }));
    await user.click(button("Delete").at(-1));
    await user.click(applyButton());

    await waitFor(() => expect(applyRequest()).toBeTruthy());
    expect(applyRequest().files).toEqual([
      { file_id: 1, answers: { 2: "remove", 3: "keep" } },
      { file_id: 2, answers: { 4: "remove", 5: "remove" } },
    ]);
  });
});
