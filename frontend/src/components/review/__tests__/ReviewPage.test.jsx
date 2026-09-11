/**
 * ReviewPage — two gates flag subtitle tracks, and each has its own setting.
 *
 * WHAT THIS PAGE GETS WRONG QUIETLY
 * ---------------------------------
 * Files reach manual review for three reasons now, and two of them flag
 * subtitle tracks: image-based subtitles that cannot become SRT, and
 * embedded fonts that only MKV can hold. They look identical in the payload
 * — both carry flagged_subtitles — and they are resolved by different
 * settings that can be set to opposite values.
 *
 * So counting them together offers to bulk-resolve font items under
 * Image-Based Subtitle Handling. With that on Always Remove, one button
 * press converts every anime file, drops its fonts and flattens its
 * typesetting to SRT — from a control that says it is resolving image
 * subtitles. Nothing fails, and the styling is gone.
 *
 * review_reason is what tells them apart. A null reason on a flagged item
 * predates that column and can only be an image-subtitle review, since the
 * font gate did not exist when it was written; this page reads it the same
 * way the bulk resolver does.
 *
 * The component had no tests at all before this file.
 *
 * Verified by mutation, 6 applied, 6 killed:
 *
 *   • Font items counted as subtitle items                  → killed
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
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReviewPage } from "../ReviewPage";
import { ThemeProvider } from "../../../theme";

const API = "http://backend";

/* One of each: an image-subtitle review, a font review, and a row written
 * before review_reason existed. */
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
    /** Every install has these: rows in review from before review_reason
     *  existed. All predate the font gate, so they are image-subtitle
     *  reviews — and counting them as font items would resolve them under
     *  a setting that has nothing to say about them. */
    mockApi({ imgSetting: "always_remove", fontSetting: "always_remove" });
    setup([LEGACY_ITEM]);

    expect(await screen.findByRole("button",
      { name: /RESOLVE ALL 1 SUBTITLE ITEMS/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /FONT ITEMS/i })).toBeNull();
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
