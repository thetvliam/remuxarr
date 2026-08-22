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
 *
 * An earlier run of that same suite reported 4/4 against a component with
 * no tests: `vitest run <path>` exits non-zero when it finds no test
 * files, which reads as every mutation dying. The numbers below are from
 * the suite that actually exists.
 */
import { render, screen, waitFor } from "@testing-library/react";
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

const setup = (items = ITEMS) => {
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

  render(
    <ThemeProvider>
    <SubtitleLanguageReviewSection api={API} toast={vi.fn()} refreshKey={0} />
    </ThemeProvider>,
  );
};

const bodyOf = (fragment) =>
  JSON.parse(calls.find(c => c.url.includes(fragment) && c.method === "POST").body);

beforeEach(() => { calls = []; });

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
