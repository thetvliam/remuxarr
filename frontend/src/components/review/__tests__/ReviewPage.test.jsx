/**
 * ReviewPage — two gates flag subtitle tracks, and each has its own setting.
 *
 * WHAT THIS PAGE GETS WRONG QUIETLY
 * ---------------------------------
 * Files reach manual review for four reasons now, and three of them flag
 * subtitle tracks: image-based subtitles that cannot become SRT, embedded
 * fonts that only MKV can hold, and text subtitles FFmpeg could not decode
 * as UTF-8. They look identical in the payload — all carry
 * flagged_subtitles — and the first two are resolved by different settings
 * that can be set to opposite values. The third has no setting at all.
 *
 * So counting them together offers to bulk-resolve font items under
 * Image-Based Subtitle Handling. With that on Always Remove, one button
 * press converts every anime file, drops its fonts and flattens its
 * typesetting to SRT — from a control that says it is resolving image
 * subtitles. Nothing fails, and the styling is gone.
 *
 * review_reason is what tells them apart. A null reason on a flagged item
 * is an image-subtitle review — QueueItem.review_reason says where those
 * rows come from — and this page reads it the same way the bulk resolver
 * does. An encoding review gets no bulk action: re-deciding the file
 * cannot see the encoding failure, so it would queue the extraction that
 * just failed.
 *
 * The component had no tests at all before this file.
 *
 * Verified by mutation, 7 applied, 7 killed:
 *
 *   • Font items counted as subtitle items                  → killed
 *   • Encoding items counted as subtitle items              → killed
 *   • Subtitle items counted as font items                  → killed
 *   • Unlabelled items treated as font items                → killed
 *   • Each button posting to the other's endpoint           → killed
 *   • The font button shown while its setting is always_ask → killed
 *   • Both buttons reading the same setting                 → killed
 *
 * A mutation run here must confirm the file was collected: `vitest run
 * <path>` exits non-zero when it finds NO test files, which is
 * indistinguishable from every mutant dying — see the note in
 * LanguageReviewSection.test.jsx, where that happened.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewPage } from "../ReviewPage";
import { ThemeProvider } from "../../../theme";

const API = "http://backend";

/* One of each: an image-subtitle review, a font review, an unlabelled
 * image-subtitle review, and a subtitle-encoding review. */
const IMAGE_ITEM = {
  id: 1, status: "manual_review", review_reason: "image_subtitles",
  flagged_subtitles: [{ stream_index: 2, language: "eng",
                        codec: "hdmv_pgs_subtitle", is_forced: false }],
  file: { id: 1, filename: "Movie.mkv", path: "/m/Movie.mkv" },
};
const FONT_ITEM = {
  id: 2, status: "manual_review", review_reason: "font_attachments",
  flagged_subtitles: [{ stream_index: 3, language: "eng", codec: "ass",
                        is_forced: false, title: "Signs and Songs" }],
  file: { id: 2, filename: "Saiki.mkv", path: "/m/Saiki.mkv" },
};
const LEGACY_ITEM = {
  id: 3, status: "manual_review", review_reason: null,
  flagged_subtitles: [{ stream_index: 2, language: "eng",
                        codec: "dvd_subtitle", is_forced: false }],
  file: { id: 3, filename: "Old.mkv", path: "/m/Old.mkv" },
};
const ENCODING_ITEM = {
  id: 4, status: "manual_review", review_reason: "subtitle_encoding",
  flagged_subtitles: [{ stream_index: 2, language: "eng",
                        codec: "subrip", is_forced: false }],
  file: { id: 4, filename: "Latin1.mkv", path: "/m/Latin1.mkv" },
};

/* An audio-type review: no flagged subtitles, so the row offers the plain
 * APPROVE / SKIP pair rather than the per-subtitle controls. This is the
 * shape that matters here — re-deciding it is what can write an
 * AudioLanguageFlag for the section further down the page. */
const AUDIO_ITEM = {
  id: 5, status: "manual_review", review_reason: null,
  flagged_subtitles: null,
  file: { id: 5, filename: "Dubbed.mkv", path: "/m/Dubbed.mkv" },
};

let posted;

const mockApi = ({ imgSetting = "always_ask", fontSetting = "always_ask" } = {}) => {
  posted = [];
  global.fetch = vi.fn(async (url, opts = {}) => {
    const u = String(url);
    if (opts.method === "POST") {
      posted.push(u);
      return { ok: true, json: async () => ({ resolved: 1, still_unresolved: 0, errors: [] }) };
    }
    if (u.includes("image_subtitle_handling"))
      return { ok: true, json: async () => ({ value: imgSetting }) };
    if (u.includes("font_attachment_handling"))
      return { ok: true, json: async () => ({ value: fontSetting }) };
    return { ok: true, json: async () => ({ items: [], total: 0 }) };
  });
};

/* jsdom has no IntersectionObserver, and both language sections below this
 * page arm one as soon as their list has more rows than the first page. The
 * effect throws a ReferenceError, React unmounts the whole section, and the
 * assertions here keep passing because they are about the bulk bar rather
 * than the sections — so the harness diverges silently from what any user
 * with more than one page of flagged files actually gets.
 *
 * mockApi answers those lists with total 0 today, which is what keeps the
 * observer out of reach. That is a property of the mock and not something
 * any test here asserts, so it is not a guarantee.
 *
 * Third copy of this stub. HistoryPanel.test.jsx and LanguageReviewSection.
 * test.jsx hold the others, and three is usually the point at which a shared
 * helper is worth extracting. Left duplicated here on purpose: this one is
 * inert scaffolding rather than a recording observer the tests interrogate,
 * so sharing it would mean exporting the richer version to a file that only
 * needs the class to exist. Worth revisiting if a fourth appears.
 */
class InertObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const setup = (items) => {
  const onRefresh = vi.fn();
  const toast = vi.fn();
  render(
    <ThemeProvider>
      <ReviewPage api={API} items={items} onRefresh={onRefresh} toast={toast}
                  invalidateHistory={vi.fn()} />
    </ThemeProvider>,
  );
  return { onRefresh, toast };
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal("IntersectionObserver", InertObserver);
});

describe("ReviewPage — refreshing the language lists", () => {
  /* Resolving a manual-review item can CREATE a language review row.
   * _apply_decision_to_item calls _upsert_language_flags for both the skipped
   * and pending outcomes, deliberately, so that a mismatch the engine noticed
   * while re-deciding is not lost. The two sections sit further down this
   * same page.
   *
   * They are paginated lists driven by usePaginatedFetch, which refetches on
   * reviewRefreshKey and on nothing else here. approve() called onRefresh()
   * and invalidateHistory(null) — the queue and History — so the row appeared
   * in the database, the page did not move, and the section only showed it
   * after navigating away and back. */

  const LANGUAGE_GET = /(audio|subtitle)-language-review/;

  /** Stands in for App.jsx: holds the key and bumps it on the callback. */
  const Harness = ({ items }) => {
    const [key, setKey] = useState(0);
    return (
      <ThemeProvider>
        <ReviewPage api={API} items={items} onRefresh={vi.fn()} toast={vi.fn()}
                    invalidateHistory={vi.fn()} reviewRefreshKey={key}
                    onReviewResolved={() => setKey(k => k + 1)} />
      </ThemeProvider>
    );
  };

  const languageGets = () =>
    global.fetch.mock.calls.filter(
      ([url, opts = {}]) => (opts.method || "GET") === "GET"
                            && LANGUAGE_GET.test(String(url))).length;

  it("refetches both language lists when an item is approved", async () => {
    mockApi();
    render(<Harness items={[AUDIO_ITEM]} />);

    await waitFor(() => expect(languageGets()).toBe(2));
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: /^APPROVE$/i }));

    await waitFor(() => expect(languageGets()).toBe(4));
  });
});

describe("ReviewPage — bulk resolving", () => {
  it("counts font items separately from image-subtitle ones", async () => {
    /** The count is what the button acts on, so a font item counted as a
     *  subtitle item is one that gets resolved under the wrong setting. */
    mockApi({ imgSetting: "always_remove", fontSetting: "always_keep" });
    setup([IMAGE_ITEM, FONT_ITEM]);

    expect(await screen.findByRole("button",
      { name: /RESOLVE ALL 1 SUBTITLE ITEMS/i })).toBeTruthy();
    expect(await screen.findByRole("button",
      { name: /RESOLVE ALL 1 FONT ITEMS/i })).toBeTruthy();
  });

  it("treats an item with no recorded reason as an image-subtitle one", async () => {
    /** Every install has these: image-subtitle reviews written before
     *  every path recorded review_reason. Counting them as font items would
     *  resolve them under a setting that has nothing to say about them. */
    mockApi({ imgSetting: "always_remove", fontSetting: "always_remove" });
    setup([LEGACY_ITEM]);

    expect(await screen.findByRole("button",
      { name: /RESOLVE ALL 1 SUBTITLE ITEMS/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /FONT ITEMS/i })).toBeNull();
  });

  it("offers no bulk action for a subtitle-encoding review", async () => {
    /** Resolving it in bulk re-decided the file, which cannot see the
     *  encoding failure, and queued the same failing extraction; the file
     *  came straight back. Counted as a subtitle item, the button offered
     *  exactly that. */
    mockApi({ imgSetting: "always_remove" });
    setup([IMAGE_ITEM, ENCODING_ITEM]);

    expect(await screen.findByRole("button",
      { name: /RESOLVE ALL 1 SUBTITLE ITEMS/i })).toBeTruthy();
  });

  it("counts correctly with a language list long enough to page", async () => {
    /* The bulk bar and the two language sections share this page, and the
     * sections page their lists independently. A library where audio or
     * subtitle review runs past its first page is the ordinary case for
     * anyone who has just scanned a season, and nothing here covered the
     * page in that state: the sections happened to be handed empty lists.
     *
     * What this guards is the silent version of the failure. If the sections
     * blow up, the bulk-bar assertions above still pass, because they never
     * look at the sections. */
    posted = [];
    global.fetch = vi.fn(async (url, opts = {}) => {
      const u = String(url);
      if (opts.method === "POST") {
        posted.push(u);
        return { ok: true, json: async () => ({ resolved: 1, still_unresolved: 0, errors: [] }) };
      }
      if (u.includes("image_subtitle_handling"))
        return { ok: true, json: async () => ({ value: "always_remove" }) };
      if (u.includes("font_attachment_handling"))
        return { ok: true, json: async () => ({ value: "always_keep" }) };
      // A first page far short of the total, so hasMore goes true and the
      // sentinel effect runs.
      return { ok: true, json: async () => ({
        total: 400,
        items: [{ id: 1, file_id: 9, filename: "X.mkv", path: "/m/X.mkv",
                  stream_index: 2, detected_language: "und",
                  extracted_path: null }],
        languages: [{ language: "und", count: 400 }],
      }) };
    });
    setup([IMAGE_ITEM, FONT_ITEM]);

    expect(await screen.findByRole("button",
      { name: /RESOLVE ALL 1 SUBTITLE ITEMS/i })).toBeTruthy();
    expect(await screen.findByRole("button",
      { name: /RESOLVE ALL 1 FONT ITEMS/i })).toBeTruthy();
    /* No separate "the sections are still mounted" assertion. One was
     * written and then removed: with the stub taken away it made no
     * difference, because a throw in either section tears down this whole
     * tree and the two button lookups above fail first. It read as though it
     * guarded something and did not. */
  });

  it("sends each button to its own endpoint", async () => {
    /** The bug in one line: a button labelled for fonts posting to
     *  resolve-subtitles-bulk resolves them under the image setting, and
     *  the response looks identical either way. */
    mockApi({ imgSetting: "always_remove", fontSetting: "always_keep" });
    const user = userEvent.setup();
    setup([IMAGE_ITEM, FONT_ITEM]);

    await user.click(await screen.findByRole("button",
      { name: /RESOLVE ALL 1 FONT ITEMS/i }));

    await waitFor(() => expect(posted).toEqual([
      `${API}/api/queue/resolve-fonts-bulk`,
    ]));
  });

  it("sends the subtitle button to the subtitle endpoint", async () => {
    mockApi({ imgSetting: "always_remove", fontSetting: "always_keep" });
    const user = userEvent.setup();
    setup([IMAGE_ITEM, FONT_ITEM]);

    await user.click(await screen.findByRole("button",
      { name: /RESOLVE ALL 1 SUBTITLE ITEMS/i }));

    await waitFor(() => expect(posted).toEqual([
      `${API}/api/queue/resolve-subtitles-bulk`,
    ]));
  });

  it("offers no font button while the setting is still always_ask", async () => {
    /** always_ask means the gate re-flags every item it is handed, so the
     *  action would report resolving nothing and leave the list unchanged.
     *  Each button reads its OWN setting: showing the font one because the
     *  image setting happens to be decisive is the same confusion in a
     *  different place. */
    mockApi({ imgSetting: "always_remove", fontSetting: "always_ask" });
    setup([IMAGE_ITEM, FONT_ITEM]);

    expect(await screen.findByRole("button",
      { name: /SUBTITLE ITEMS/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /FONT ITEMS/i })).toBeNull();
  });

  it("offers no subtitle button while that setting is still always_ask", async () => {
    mockApi({ imgSetting: "always_ask", fontSetting: "always_keep" });
    setup([IMAGE_ITEM, FONT_ITEM]);

    expect(await screen.findByRole("button",
      { name: /FONT ITEMS/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /SUBTITLE ITEMS/i })).toBeNull();
  });
});
