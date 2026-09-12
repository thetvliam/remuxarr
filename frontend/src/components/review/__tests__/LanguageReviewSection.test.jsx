/**
 * LanguageReviewSection — resolving flagged language tags.
 *
 * WHAT THIS PAGE GETS WRONG QUIETLY
 * ---------------------------------
 * A file can have several undefined subtitle tracks, each extracted to its
 * own .srt carrying the language in its filename. Answering is therefore
 * per TRACK, while ignoring is per FILE — "stop asking me about this one"
 * is a decision about the file. Those two go to the same button bar over
 * the same selection, so it is easy for one of them to send the other's
 * ids and for nothing to look wrong: the request succeeds, the list
 * refreshes, and either one track was silenced when the whole file should
 * have been, or a whole file was renamed when one track was meant.
 *
 * The component had no tests at all, which is how it acquired a selection
 * keyed on file_id while the rows it rendered were per track.
 *
 * Verified by mutation, 4 applied, 4 killed:
 *
 *   • Apply sending file ids instead of flag ids        → killed
 *   • Selection keyed on the file, so two tracks of one
 *     file could not be told apart                       → killed
 *   • Ignore sending flag ids, silencing one track       → killed
 *   • Grouping collapsing every file into one            → killed
 *   • Grouping keyed on adjacency, so one file split in two → killed
 *
 * An earlier run of that same suite reported 4/4 against a component with
 * no tests: `vitest run <path>` exits non-zero when it finds no test
 * files, which reads as every mutation dying. The numbers below are from
 * the suite that actually exists.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SubtitleLanguageReviewSection } from "../SubtitleLanguageReviewSection";
import { ThemeProvider } from "../../../theme";

const API = "http://backend";

/* One file, three undefined subtitles — the reported shape. */
const ITEMS = [
  { id: 11, file_id: 7, filename: "Show.mkv", path: "/m/Show.mkv",
    stream_index: 2, detected_language: "und",
    extracted_path: "/m/Show.und.forced.srt" },
{ id: 12, file_id: 7, filename: "Show.mkv", path: "/m/Show.mkv",
  stream_index: 3, detected_language: "und",
  extracted_path: "/m/Show.und.dub.srt" },
{ id: 13, file_id: 7, filename: "Show.mkv", path: "/m/Show.mkv",
  stream_index: 4, detected_language: "und",
  extracted_path: "/m/Show.und.sdh.srt" },
];

let calls;

const setup = (items = ITEMS, reviewRefreshKey = 0) => {
  calls = [];
  global.fetch = vi.fn(async (url, options = {}) => {
    calls.push({ url: String(url), method: options.method || "GET",
      body: options.body });
    if ((options.method || "GET") !== "GET") {
      return { ok: true, json: async () => ({ applied: 1, ignored: 1 }) };
    }
    return {
      ok: true,
      json: async () => ({ total: items.length, items,
        languages: [{ language: "und", count: items.length }] }),
    };
  });

  return render(
    <ThemeProvider>
    <SubtitleLanguageReviewSection api={API} toast={vi.fn()}
                                   reviewRefreshKey={reviewRefreshKey} />
    </ThemeProvider>,
  );
};

/** GET requests issued so far — one per list fetch. */
const listFetches = () => calls.filter(c => c.method === "GET").length;

const bodyOf = (fragment) =>
JSON.parse(calls.find(c => c.url.includes(fragment) && c.method === "POST").body);

/* A second mock rather than a `total` parameter on the one above.
 *
 * setup() answers every page with the same body and reports `total` equal to
 * the rows it returns, so hasMore is always false and the scroll path is
 * unreachable through it. Teaching it to page would change what every test
 * above is running against for the sake of the ones below — and those tests
 * depend on the list being complete: their selections assume the rows on
 * screen are all the rows there are. */
/** Resolvers for page requests the mock was told to hold, in request order. */
let pending;

const setupPaged = ({ total, page, holdAfter = Infinity }) => {
  calls = [];
  pending = [];
  let served = 0;
  global.fetch = vi.fn(async (url, options = {}) => {
    const u = String(url);
    calls.push({ url: u, method: options.method || "GET", body: options.body });
    if ((options.method || "GET") !== "GET") {
      return { ok: true, json: async () => ({ applied: 1, ignored: 1 }) };
    }
    const offset = Number(new URL(u).searchParams.get("offset") || 0);
    const items = Array.from(
      { length: Math.max(0, Math.min(page, total - offset)) },
      (_, n) => {
        const index = offset + n;
        return {
          id: 100 + index, file_id: 100 + index,
          filename: `Ep${index}.mkv`, path: `/m/Ep${index}.mkv`,
          stream_index: 2, detected_language: "und",
          extracted_path: `/m/Ep${index}.und.srt`,
        };
      },
    );
    const body = { ok: true, json: async () => ({ total, items,
      languages: [{ language: "und", count: total }] }) };
    served += 1;
    /* An immediately-resolving mock never lets a render commit with `loading`
     * true: the whole request settles inside one React batch, so the component
     * goes straight from one loaded list to the next and the transition the
     * re-arm test is about does not exist in the harness. Holding the page
     * open is what makes it observable — it is a real network having latency,
     * which is the ordinary case rather than the exotic one. */
    if (served > holdAfter) {
      return new Promise(resolve => pending.push(() => resolve(body)));
    }
    return body;
  });

  return render(
    <ThemeProvider>
    <SubtitleLanguageReviewSection api={API} toast={vi.fn()} reviewRefreshKey={0} />
    </ThemeProvider>,
  );
};

/* jsdom has no IntersectionObserver, so the sentinel effect throws a
 * ReferenceError the moment hasMore goes true and the whole section unmounts.
 * HistoryPanel.test.jsx carries the only other copy of this; it is duplicated
 * rather than shared because extracting it would mean editing that file for a
 * change that has nothing to do with it.
 *
 * Stubbed for the whole file, not only the scroll tests below. The tests above
 * happen not to construct one — they report `total` equal to the rows they
 * return — but that is a property of their mock, not something they assert,
 * and without the stub they would start failing with a ReferenceError the day
 * it changed, for a reason unrelated to what they cover. */
let observers;

class FakeObserver {
  constructor(cb) { this.cb = cb; this.targets = []; observers.push(this); }
  observe(el) { this.targets.push(el); }
  unobserve(el) { this.targets = this.targets.filter(t => t !== el); }
  disconnect() { this.targets = []; }
}

/** Whether anything is currently watching for the end of the list. */
const watching = () => observers.some(o => o.targets.length > 0);

beforeEach(() => {
  calls = [];
  observers = [];
  vi.stubGlobal("IntersectionObserver", FakeObserver);
});

describe("per-track rows", () => {
  it("shows every flagged track of a file, not just the first", async () => {
    setup();

    // The sidecar name is what distinguishes them — a stream index does
    // not tell anyone which one is the forced subtitle.
    expect(await screen.findByText("Show.und.forced.srt")).toBeTruthy();
    expect(screen.getByText("Show.und.dub.srt")).toBeTruthy();
    expect(screen.getByText("Show.und.sdh.srt")).toBeTruthy();
  });

  it("shows the filename once, not on every row", async () => {
    setup();
    await screen.findByText("Show.und.forced.srt");

    expect(screen.getAllByText("Show.mkv")).toHaveLength(1);
  });

  it("selects tracks independently", async () => {
    setup();
    const user = userEvent.setup();

    const boxes = await screen.findAllByRole("checkbox");
    // The first is the select-all in the header bar.
    await user.click(boxes[1]);

    expect(boxes[1].checked).toBe(true);
    expect(boxes[2].checked).toBe(false);
  });
});

describe("applying", () => {
  it("sends the selected flag ids", async () => {
    setup();
    const user = userEvent.setup();

    const boxes = await screen.findAllByRole("checkbox");
    await user.click(boxes[2]);            // the dub track, flag 12
    // Cleared first: the field is pre-filled, so typing appends and the
    // request goes out with "engeng".
    await user.clear(screen.getByPlaceholderText(/eng/i));
    await user.type(screen.getByPlaceholderText(/eng/i), "eng");
    await user.click(screen.getByRole("button", { name: /SET LANGUAGE/ }));

    await waitFor(() => expect(calls.some(c => c.url.includes("/apply"))).toBe(true));
    expect(bodyOf("/apply")).toEqual({ flag_ids: [12], target_language: "eng" });
  });

  it("answers only the tracks that were selected", async () => {
    setup();
    const user = userEvent.setup();

    const boxes = await screen.findAllByRole("checkbox");
    await user.click(boxes[1]);
    await user.click(boxes[3]);
    // Cleared first: the field is pre-filled, so typing appends and the
    // request goes out with "engeng".
    await user.clear(screen.getByPlaceholderText(/eng/i));
    await user.type(screen.getByPlaceholderText(/eng/i), "eng");
    await user.click(screen.getByRole("button", { name: /SET LANGUAGE/ }));

    await waitFor(() => expect(calls.some(c => c.url.includes("/apply"))).toBe(true));
    expect(bodyOf("/apply").flag_ids.sort()).toEqual([11, 13]);
  });
});

describe("ignoring", () => {
  it("sends file ids, not flag ids", async () => {
    /**
     * Ignore is a per-file decision. Sending the selected flag ids would
     * silence one track and leave the rest of the file still asking —
     * and the request would succeed, so nothing would look wrong until
     * the same file came back on the next scan.
     */
    setup();
    const user = userEvent.setup();

    const boxes = await screen.findAllByRole("checkbox");
    await user.click(boxes[1]);
    await user.click(screen.getByRole("button", { name: /IGNORE/ }));

    await waitFor(() => expect(calls.some(c => c.url.includes("/ignore"))).toBe(true));
    expect(bodyOf("/ignore")).toEqual({ file_ids: [7] });
  });

  it("collapses several selected tracks of one file to a single id", async () => {
    setup();
    const user = userEvent.setup();

    const boxes = await screen.findAllByRole("checkbox");
    await user.click(boxes[1]);
    await user.click(boxes[2]);
    await user.click(screen.getByRole("button", { name: /IGNORE/ }));

    await waitFor(() => expect(calls.some(c => c.url.includes("/ignore"))).toBe(true));
    expect(bodyOf("/ignore")).toEqual({ file_ids: [7] });
  });
});

describe("grouping", () => {
  it("keeps separate files apart", async () => {
    setup([
      ITEMS[0],
      { id: 21, file_id: 8, filename: "Other.mkv", path: "/m/Other.mkv",
        stream_index: 2, detected_language: "und",
        extracted_path: "/m/Other.und.srt" },
    ]);

    expect(await screen.findByText("Show.mkv")).toBeTruthy();
    expect(screen.getByText("Other.mkv")).toBeTruthy();
    expect(screen.getByText("Show.und.forced.srt")).toBeTruthy();
    expect(screen.getByText("Other.und.srt")).toBeTruthy();
  });

  it("falls back to the stream index when a track was not extracted", async () => {
    // An embedded track has no sidecar; there is still something to name.
    setup([{ ...ITEMS[0], extracted_path: null }]);

    expect(await screen.findByText("Stream 2")).toBeTruthy();
  });
});


describe("grouping", () => {
  /* Rows for one file are not guaranteed to arrive next to each other.
   *
   * They normally do — the backend orders by (filename, stream_index), and
   * appending a page keeps that intact because the next page continues
   * where the last stopped. The list changing underneath the offset is
   * what breaks it: a scan flagging files that sort earlier shifts
   * everything back, loadMore() returns rows already rendered above, and a
   * file that was complete shows up a second time.
   *
   * Grouping on adjacency then emits two groups with the same file_id.
   * React sees a duplicate key and reconciles them as one element, so the
   * second group's checkbox state lands on the first and ticking a track
   * can select a different one. */
  const SPLIT = [
    { id: 11, file_id: 7, filename: "Show.mkv", path: "/m/Show.mkv",
      stream_index: 2, detected_language: "und" },
      { id: 21, file_id: 8, filename: "Other.mkv", path: "/m/Other.mkv",
        stream_index: 2, detected_language: "und" },
         // Same file as the first row, arriving after an unrelated one.
         { id: 12, file_id: 7, filename: "Show.mkv", path: "/m/Show.mkv",
           stream_index: 3, detected_language: "und" },
  ];

  it("puts non-adjacent rows of one file in a single group", async () => {
    setup(SPLIT);

    // Two files, so two filename headings — not three groups from three
    // rows, and not two headings for Show.mkv.
    await waitFor(() => expect(screen.getAllByText("Show.mkv").length).toBe(1));
    expect(screen.getAllByText("Other.mkv").length).toBe(1);
  });

  it("keeps both of the split file's tracks selectable apart", async () => {
    /* The consequence worth pinning, rather than the group count: under a
     * duplicate key the second group's checkbox drives the first, so this
     * selects the wrong track. */
    setup(SPLIT);
    const user = userEvent.setup();

    const boxes = await screen.findAllByRole("checkbox");
    /* boxes[0] is the header select-all. Grouped by file id, Show.mkv
     * owns both its rows and renders first, so boxes[2] is its stream 3 —
     * the row that arrived after an unrelated file. Grouped by adjacency
     * the same index is Other.mkv's row instead, which is what makes this
     * index the one worth asserting on. */
    await user.click(boxes[2]);
    await user.click(screen.getByRole("button", { name: /IGNORE/i }));

    expect(bodyOf("ignore").file_ids).toEqual([7]);
  });
});


describe("refresh signals", () => {
  /* Two independent signals are combined into one key for the shared hook:
   * `refreshKey` is local and bumped after this section's own Apply/Ignore,
   * while `reviewRefreshKey` arrives from the WebSocket layer when a scan, a
   * webhook-queued file or a finished job may have written new flag rows.
   *
   * Neither had a test. This file was passing `refreshKey={0}` — not a prop
   * this component takes — so it was spread through and dropped, and the
   * suite would not have noticed either half of the key being deleted.
   *
   * Mutation, 3 applied against the suite before these tests, 3 survived:
   *
   *   • reviewRefreshKey dropped from the combined key → killed
   *   • The local refreshKey dropped from it           → killed
   *   • The `reviewRefreshKey = 0` default removed     → EQUIVALENT
   *
   * The default is unkillable because omitting the prop yields a combined key
   * of "undefined:0", which is every bit as stable as "0:0" — nothing reads
   * the value, only whether it changed. It is defensive rather than
   * load-bearing, and the only caller passes the prop. */

  it("refetches the list when the external refresh key changes", async () => {
    /* Without this signal a scan can surface twenty new mismatches while the
     * section keeps showing whatever it fetched on mount, until the page is
     * navigated away from and back. */
    const { rerender } = setup();
    await waitFor(() => expect(listFetches()).toBe(1));

    rerender(
      <ThemeProvider>
      <SubtitleLanguageReviewSection api={API} toast={vi.fn()} reviewRefreshKey={1} />
      </ThemeProvider>,
    );

    await waitFor(() => expect(listFetches()).toBe(2));
  });

  it("refetches the list after applying a language", async () => {
    /* The local half of the same key. The answered rows are deleted server
     * side, so a list that does not refetch keeps offering tracks whose
     * question has already been settled. */
    setup();
    const user = userEvent.setup();

    const boxes = await screen.findAllByRole("checkbox");
    await waitFor(() => expect(listFetches()).toBe(1));
    await user.click(boxes[1]);
    await user.click(screen.getByRole("button", { name: /SET LANGUAGE/i }));

    await waitFor(() => expect(listFetches()).toBe(2));
  });
});


describe("infinite scroll", () => {
  /* The page size here is 100, chosen so that "search a show name, select all
   * matching episodes" fits one fetch. A long-running show exceeds it, which
   * is the only way the rest of a list is ever reached. None of it had
   * coverage: jsdom has no IntersectionObserver, so any test that let hasMore
   * go true died in the effect rather than in an assertion.
   *
   * Mutation, 8 applied against the unchanged suite. Two were already killed
   * by usePaginatedFetch's own tests — loadMore refetching offset 0, and
   * hasMore dropping its empty-page term — so they are recorded here as
   * already covered rather than claimed below. Of the six that survived:
   *
   *   • The `loading` dependency dropped               → killed
   *   • isIntersecting inverted                        → killed
   *   • The loadMore() call deleted                    → killed
   *   • The sentinel's disconnect cleanup removed      → killed
   *   • observe(scroll) in place of observe(sentinel)  → killed
   *   • The effect's `!hasMore` term dropped           → EQUIVALENT
   *
   * That last one cannot be killed alone, and neither can its mirror: the
   * sentinel is itself rendered only under `hasMore`, so with either guard
   * present the other is unreachable — dropping the effect's term leaves
   * sentinelRef null, and rendering the sentinel unconditionally leaves the
   * effect returning early. Both were applied separately and both survived.
   * Removing BOTH is killed by "does not watch when the list is already
   * complete", which is the test that makes the pair load-bearing rather
   * than either guard on its own. */

  it("watches the sentinel, not the scroll container", async () => {
    /* Observing the scroll container instead loads the next page as soon as
     * the list is on screen at all, so the whole list arrives at once and the
     * pagination is decorative. Both are "something is being observed", which
     * is why this asserts on which element. */
    setupPaged({ total: 300, page: 100 });

    // The observer is armed by a passive effect that runs after the commit
    // putting the sentinel in the DOM, so wait for the arming rather than for
    // a row — see the same note in HistoryPanel.test.jsx.
    await waitFor(() => expect(watching()).toBe(true));

    const [target] = observers.at(-1).targets;
    // The sentinel sits below the rows and holds none of them.
    expect(target.querySelector("input[type=checkbox]")).toBeNull();
    expect(target.textContent).not.toContain("Ep0.mkv");
  });

  it("loads the next page when the sentinel comes into view", async () => {
    setupPaged({ total: 300, page: 100 });
    await waitFor(() => expect(watching()).toBe(true));

    await act(async () => {
      observers.at(-1).cb([{ isIntersecting: true }]);
    });

    await waitFor(() => expect(screen.getByText("Ep100.und.srt")).toBeInTheDocument());
    // Still the first page's rows too — the page appended rather than replaced.
    expect(screen.getByText("Ep0.und.srt")).toBeInTheDocument();
  });

  it("loads nothing while the sentinel is out of view", async () => {
    /* The negative half of the test above. Without it, a callback that called
     * loadMore() unconditionally would pass every other test here. */
    setupPaged({ total: 300, page: 100 });
    await waitFor(() => expect(watching()).toBe(true));
    const before = calls.filter(c => c.method === "GET").length;

    await act(async () => {
      observers.at(-1).cb([{ isIntersecting: false }]);
    });

    expect(calls.filter(c => c.method === "GET").length).toBe(before);
  });

  it("does not watch when the list is already complete", async () => {
    /* Armed against a list with nothing left to fetch, the sentinel is not
     * rendered and the observer has nothing to hold — but the guard is what
     * stops it being constructed at all, and a list that keeps asking for
     * pages past its end is the failure it prevents. */
    setupPaged({ total: 3, page: 100 });

    await waitFor(() => expect(screen.getByText("Ep0.und.srt")).toBeInTheDocument());
    expect(watching()).toBe(false);
  });

  it("re-arms after a page finishes loading", async () => {
    /* `loading` is in that effect's dependency array and the effect body never
     * reads it, so it reads as a stray dep that a tidy-up would remove —
     * exhaustive-deps is an error in this project and does not flag it either.
     * It is load-bearing. A real IntersectionObserver reports intersection
     * when it first observes an element and then only on CHANGES, so a
     * sentinel that is still in view after a page lands never fires again.
     * Re-running on the loading transition builds a fresh observer, which
     * reports the current state, and that is what keeps a long list paging
     * without the user scrolling further. */
    setupPaged({ total: 300, page: 100, holdAfter: 1 });
    await waitFor(() => expect(watching()).toBe(true));
    const armedFirst = observers.length;

    await act(async () => {
      observers.at(-1).cb([{ isIntersecting: true }]);
    });

    // The second page is still in flight, so this is the loading transition
    // itself rather than the list changing underneath it.
    await waitFor(() => expect(observers.length).toBeGreaterThan(armedFirst));
    expect(observers.at(-1).targets).toHaveLength(1);

    await act(async () => { pending.shift()(); });
    await waitFor(() => expect(screen.getByText("Ep100.und.srt")).toBeInTheDocument());
  });

  it("stops watching when the section unmounts", async () => {
    /* Without the disconnect, the observer outlives the component it was
     * built for and keeps a reference to its callback — which closes over
     * loadMore, and through it the hook's state. */
    const { unmount } = setupPaged({ total: 300, page: 100 });
    await waitFor(() => expect(watching()).toBe(true));

    unmount();

    expect(watching()).toBe(false);
  });
});
