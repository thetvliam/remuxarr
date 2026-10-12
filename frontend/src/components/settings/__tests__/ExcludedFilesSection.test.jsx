/**
 * ExcludedFilesSection — the list of files the exclude settings pass over.
 *
 * A scan's toast gave only a count of excluded files, and nothing about them
 * is stored, so this asks GET /api/scan/excluded on demand and lists each
 * file with what matched it.
 *
 * The two files every response here carries differ in name, path and reason,
 * so a row showing another row's reason is visible. Each response is a fresh
 * copy: a mock handing back the same object can let a re-render be skipped
 * and a test pass without the update it was meant to check.
 *
 * The last two tests go through SettingsPage, because the panel is only ever
 * reached that way: they are what sees the page fail to render it, or render
 * it without the server address or the toast.
 *
 * Verified by mutation. New code, so two mutants were first run against the
 * unchanged 596-test suite only to show the harness reached it, and both
 * survived: every row given the first row's reason, and the page not
 * rendering the panel. Nine were run against the whole suite once these
 * tests existed: those two; the page not passing the server address; the
 * page not passing the toast; the server's reason replaced by the generic
 * one; a refusal shown as an empty list; the path shown as the filename; the
 * count off by one; and the old list kept while a new check runs. All nine
 * are killed, each by the test written for it.
 *
 * When Always Include was added the list moved below it, so the page tests'
 * fixture gained that field, as the real schema has, and the placement test
 * now asserts Exclude Patterns, Always Include, the list, then the next
 * field. The list left under Exclude Patterns is killed by it; that mutant is
 * recorded with the rest of Always Include's in test_exclude_scan.py.
 *
 * The Include button came after that. Every row given the first row's entry
 * was run against the unchanged 604-test suite as a harness check, and
 * survived. Six run against the whole suite once the tests existed: that
 * one; a row never shown as added; every row shown as added; the page not
 * passing the callback; the page not passing its staged list; and the
 * callback replacing the Always Include list instead of adding to it. All
 * six are killed, each by the test written for it.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ExcludedFilesSection } from "../ExcludedFilesSection";
import { SettingsPage } from "../SettingsPage";
import { ThemeProvider } from "../../../theme";

const TWO = {
  total: 2,
  items: [
    { path: "/media/movies/Avatar (2009)/Avatar (2009)-featurette.mkv",
      filename: "Avatar (2009)-featurette.mkv",
      reason: 'Skip Trailers and Extras: name ends in "-featurette"',
      include_as: "/Avatar (2009)/Avatar (2009)-featurette.mkv" },
    { path: "/media/tv/Show/Season 01/Show - S01E01 - 3D.mkv",
      filename: "Show - S01E01 - 3D.mkv",
      reason: "Exclude Patterns: '* - 3D.mkv'",
      include_as: "/Show/Season 01/Show - S01E01 - 3D.mkv" },
  ],
};

const ok = (body) => ({ ok: true, status: 200, json: async () => structuredClone(body) });
const refused = (status, body) => ({
  ok: false, status,
  json: body === undefined ? async () => { throw new SyntaxError("not json"); }
                           : async () => structuredClone(body),
});

/** fetch answering /api/scan/excluded with each of `answers` in turn. */
function serve(...answers) {
  const queue = [...answers];
  const fetchMock = vi.fn(async () => {
    const next = queue.shift();
    if (next instanceof Error) throw next;
    return next;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderSection(toast = vi.fn(), props = {}) {
  render(
    <ThemeProvider>
      <ExcludedFilesSection api="" toast={toast} {...props} />
    </ThemeProvider>,
  );
  return toast;
}

const show = (button = screen.getByRole("button", { name: /show excluded files/i })) => {
  fireEvent.click(button);
  expect(screen.getByRole("button", { name: /checking/i })).toBeTruthy();
};

/* The button reads CHECKING… from the click until the check has finished,
 * toast and all, so its label coming back is rendered state that marks the
 * end of a check. Waiting on that rather than on the toast spy follows the
 * suite's convention (tests/README.md). show() confirms the label changed,
 * or this wait would pass before the check had even started. */
const settled = () => waitFor(() =>
  expect(screen.getByRole("button", { name: /show excluded files/i })).toBeTruthy());

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("ExcludedFilesSection", () => {
  it("asks for nothing until the button is pressed", () => {
    const fetchMock = serve(ok(TWO));
    renderSection();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("lists each file with its own reason and path", async () => {
    serve(ok(TWO));
    renderSection();

    show();

    const rows = await screen.findAllByRole("listitem");
    expect(rows).toHaveLength(2);
    TWO.items.forEach((item, i) => {
      const row = within(rows[i]);
      expect(row.getByText(item.filename)).toBeTruthy();
      expect(row.getByText(item.reason)).toBeTruthy();
      expect(row.getByText(item.path)).toBeTruthy();
    });
    expect(screen.getByText("2 EXCLUDED FILES")).toBeTruthy();
  });

  it("says so when nothing is excluded", async () => {
    serve(ok({ total: 0, items: [] }));
    renderSection();

    show();

    expect(await screen.findByText("No files are excluded.")).toBeTruthy();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("shows the server's reason when it refuses, not an empty list", async () => {
    const detail = "No scan paths configured, so there are no libraries to exclude files from. Add a library path first.";
    serve(refused(400, { detail }));
    const toast = renderSection();

    show();
    await settled();

    expect(toast).toHaveBeenCalledWith(detail, "error");
    expect(screen.queryByText("No files are excluded.")).toBeNull();
  });

  it("says it failed when the server gives no reason", async () => {
    serve(refused(500));
    const toast = renderSection();

    show();
    await settled();

    expect(toast).toHaveBeenCalledWith("Failed to list excluded files", "error");
  });

  it("does not leave an old list up when a new check fails", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    serve(ok(TWO), new TypeError("Failed to fetch"));
    const toast = renderSection();
    show();
    await screen.findByText(TWO.items[0].filename);

    show();
    await settled();

    expect(toast).toHaveBeenCalledWith("Failed to list excluded files", "error");
    expect(screen.queryByText(TWO.items[0].filename)).toBeNull();
  });
});

describe("ExcludedFilesSection — Include", () => {
  it("hands over each row's own entry", async () => {
    serve(ok(TWO));
    const onInclude = vi.fn();
    renderSection(vi.fn(), { onInclude });
    show();

    fireEvent.click(await screen.findByRole("button", { name: `Include ${TWO.items[1].filename}` }));
    fireEvent.click(screen.getByRole("button", { name: `Include ${TWO.items[0].filename}` }));

    expect(onInclude.mock.calls).toEqual([[TWO.items[1].include_as], [TWO.items[0].include_as]]);
  });

  it("shows a row already in Always Include as added, with no button", async () => {
    serve(ok(TWO));
    renderSection(vi.fn(), { included: [TWO.items[0].include_as], onInclude: vi.fn() });
    show();

    const rows = await screen.findAllByRole("listitem");
    expect(within(rows[0]).getByText("ADDED")).toBeTruthy();
    expect(within(rows[0]).queryByRole("button")).toBeNull();
    expect(within(rows[1]).queryByText("ADDED")).toBeNull();
    expect(within(rows[1]).getByRole("button", { name: `Include ${TWO.items[1].filename}` })).toBeTruthy();
  });
});

describe("SettingsPage — the excluded-files list", () => {
  /* Real group and key names, in the real schema's order: the page matches
   * both by string. A field after Always Include shows where in the group the
   * list lands. */
  const SCHEMA = [
    { key: "scan_paths", label: "Media Library Paths", type: "string_list", group: "Library" },
    { key: "exclude_patterns", label: "Exclude Patterns", type: "string_list", group: "Library" },
    { key: "include_patterns", label: "Always Include", type: "string_list", group: "Library" },
    { key: "prefer_mp4_container", label: "Prefer MP4 Container", type: "boolean", group: "Library" },
  ];
  const VALUES = { scan_paths: ["/media/movies"], exclude_patterns: [],
                   include_patterns: ["/Kept/Kept.mkv"], prefer_mp4_container: false };

  function mountPage(excludedAnswer) {
    const fetchMock = vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/api/scan/excluded")) return excludedAnswer;
      if (u.includes("/api/settings/schema")) return ok(SCHEMA);
      if (u.match(/\/api\/settings\/?$/)) return ok(VALUES);
      return ok({});
    });
    vi.stubGlobal("fetch", fetchMock);
    localStorage.setItem("remuxarr.settingsCategory", "processing");
    const toast = vi.fn();
    render(
      <ThemeProvider>
        <SettingsPage api="http://remuxarr" toast={toast} />
      </ThemeProvider>,
    );
    return { fetchMock, toast };
  }

  it("sits under Always Include, below Exclude Patterns, and asks the page's server", async () => {
    const { fetchMock } = mountPage(ok(TWO));
    const button = await screen.findByRole("button", { name: /show excluded files/i });

    const before = (a, b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    const patterns = screen.getByText("Exclude Patterns");
    const include = screen.getByText("Always Include");
    const mp4 = screen.getByText("Prefer MP4 Container");
    expect(before(patterns, include)).toBe(true);
    expect(before(include, button)).toBe(true);
    expect(before(button, mp4)).toBe(true);

    show(button);

    expect(await screen.findByText(TWO.items[1].reason)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith("http://remuxarr/api/scan/excluded");
  });

  it("hands the list the page's toast", async () => {
    const { toast } = mountPage(refused(400, { detail: "No scan paths configured." }));
    show(await screen.findByRole("button", { name: /show excluded files/i }));
    await settled();

    expect(toast).toHaveBeenCalledWith("No scan paths configured.", "error");
  });

  it("Include adds the entry to Always Include as an unsaved edit", async () => {
    const { fetchMock } = mountPage(ok(TWO));
    show(await screen.findByRole("button", { name: /show excluded files/i }));
    const entry = TWO.items[1].include_as;

    fireEvent.click(await screen.findByRole("button", { name: `Include ${TWO.items[1].filename}` }));

    // Both in the box: the one already there and the one just added.
    expect(screen.getByRole("button", { name: "Remove /Kept/Kept.mkv" })).toBeTruthy();
    expect(screen.getByRole("button", { name: `Remove ${entry}` })).toBeTruthy();
    expect(screen.getByText(/1 unsaved change$/i)).toBeTruthy();
    // The row now reads as added, from the page's staged list.
    const row = screen.getByText(TWO.items[1].filename).closest("li");
    expect(within(row).getByText("ADDED")).toBeTruthy();
    // Staged, not written.
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false);
  });

  it("saving sends the added entry with the ones already there", async () => {
    const { fetchMock } = mountPage(ok(TWO));
    show(await screen.findByRole("button", { name: /show excluded files/i }));
    fireEvent.click(await screen.findByRole("button", { name: `Include ${TWO.items[0].filename}` }));

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([, i]) => i?.method === "PUT");
    expect(JSON.parse(init.body)).toEqual({
      include_patterns: ["/Kept/Kept.mkv", TWO.items[0].include_as],
    });
  });
});
