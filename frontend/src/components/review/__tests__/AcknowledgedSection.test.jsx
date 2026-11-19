/**
 * ACKNOWLEDGED THRESHOLDS SECTION
 *
 * Files whose undefined audio tracks the user confirmed are correct as they
 * are — from Audio Language Review, or a past Approve on a review the
 * threshold raised while it still held files.
 *
 * The confirmation is set in one place, to True, and nothing ever set it back,
 * so until these endpoints existed a file could be exempt for good with
 * nowhere to see it. The Approve button that set it used to claim it would
 * process the file, which was wrong whenever nothing else needed doing — so
 * an unknown number of these were given on a false description.
 *
 * Verified by mutation, 6 applied, 6 killed:
 *
 *   • The empty-list early return removed, so the heading always shows → killed
 *   • The early return inverted, hiding the section when it HAS rows   → killed
 *   • clearSelected posting every listed id rather than the selection  → killed
 *   • The toast reporting the request size instead of data.cleared     → killed
 *   • The reload after a successful clear dropped                      → killed
 *   • The truncation notice firing when nothing is truncated           → killed
 *
 * Two are worth reading before editing this file.
 *
 * Posting every listed id rather than the selection passes any test that
 * only checks the ticked file is gone, so an unticked file is asserted to
 * survive instead.
 *
 * Dropping the reload SURVIVED until "refetches the list after clearing"
 * was written. Without it the cleared files stay on screen and the action
 * looks like it did nothing — which is the failure this whole section
 * exists to fix, reappearing one level up.
 *
 * Later, when a reload stopped unticking everything and blanking the rows,
 * 5 more, each surviving all 791 frontend tests first, 5 killed by the
 * tests under "across reloads":
 *
 *   • A reload dropping the whole selection again                      → killed
 *   • A reload keeping the ticks of files no longer listed             → killed
 *   • The pruning inverted, keeping only the files that left           → killed
 *   • "Loading…" replacing the rows on every reload again              → killed
 *   • "Loading…" never shown, even before the first list arrives       → killed
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AcknowledgedSection } from "../AcknowledgedSection";

const API = "http://x";

const FILES = [
  { id: 1, path: "/media/tv/A.mkv", filename: "A.mkv", status: "skipped" },
  { id: 2, path: "/media/tv/B.mkv", filename: "B.mkv", status: "skipped" },
];

let posted;

function mockApi({ files = FILES, total = files.length, clearOk = true, cleared = null } = {}) {
  posted = [];
  global.fetch = vi.fn(async (url, opts = {}) => {
    const u = String(url);
    if (opts.method === "POST") {
      posted.push(JSON.parse(opts.body));
      if (!clearOk) return { ok: false, status: 500, json: async () => ({}) };
      const body = JSON.parse(opts.body);
      return {
        ok: true,
        json: async () => ({
          cleared: cleared ?? body.file_ids.length,
          missing: [],
        }),
      };
    }
    if (u.includes("/acknowledged")) return { ok: true, json: async () => ({ total, files }) };
    return { ok: true, json: async () => ({}) };
  });
}

const setup = () => {
  const toast = vi.fn();
  render(<AcknowledgedSection api={API} toast={toast} refreshKey={0} />);
  return { toast };
};

beforeEach(() => { vi.restoreAllMocks(); });

describe("AcknowledgedSection", () => {
  it("lists the exempted files with the real total", async () => {
    mockApi();
    setup();

    expect(await screen.findByText("A.mkv")).toBeTruthy();
    expect(screen.getByText("B.mkv")).toBeTruthy();
  });

  it("stays out of the way when nothing is exempted", async () => {
    /* Unlike the lists above it, this describes a problem most libraries do
     * not have. A permanent "none exempted" heading would be clutter on the
     * page it exists to keep readable. */
    mockApi({ files: [], total: 0 });
    setup();

    await waitFor(() =>
      expect(screen.queryByText(/APPROVED UNDEFINED-AUDIO THRESHOLDS/i)).toBeNull());
  });

  it("clears only the files that were ticked", async () => {
    /* A version that ignores the selection and clears everything passes any
     * test that only checks the ticked file is gone. */
    mockApi();
    setup();
    const user = userEvent.setup();

    await screen.findByText("A.mkv");
    const boxes = screen.getAllByRole("checkbox");
    await user.click(boxes[0]);
    await user.click(screen.getByRole("button", { name: /CLEAR CONFIRMATION/i }));

    await waitFor(() => expect(posted.length).toBe(1));
    expect(posted[0].file_ids).toEqual([1]);
  });

  it("counts files on the button, matching the unit it sends", async () => {
    mockApi();
    setup();
    const user = userEvent.setup();

    await screen.findByText("A.mkv");
    await user.click(screen.getAllByRole("checkbox")[0]);

    expect(screen.getByRole("button", { name: /CLEAR CONFIRMATION/i }).textContent)
      .toContain("CLEAR CONFIRMATION (1 file)");
  });

  it("reports what the server cleared, not what was asked for", async () => {
    /* The list can move under a multi-select, so fewer files may actually
     * have carried the flag than were ticked. Reporting the request size
     * would overstate the work. */
    mockApi({ cleared: 1 });
    const { toast } = setup();
    const user = userEvent.setup();

    await screen.findByText("A.mkv");
    await user.click(screen.getAllByRole("checkbox")[0]);
    await user.click(screen.getAllByRole("checkbox")[1]);
    await user.click(screen.getByRole("button", { name: /CLEAR CONFIRMATION/i }));

    await waitFor(() => expect(toast).toHaveBeenCalled());
    const [message] = toast.mock.calls.at(-1);
    // Two ticked, one actually carried the flag. The message must say one.
    expect(message).toMatch(/^1 file /);
    expect(message).not.toMatch(/^2 files/);
  });

  it("says the file returns on the next scan, not immediately", async () => {
    /* The endpoint invalidates the scan stamp rather than reprocessing on
     * the spot, so the wording has to match what actually happens — the
     * same promise Skip makes. */
    mockApi({ cleared: 1 });
    const { toast } = setup();
    const user = userEvent.setup();

    await screen.findByText("A.mkv");
    await user.click(screen.getAllByRole("checkbox")[0]);
    await user.click(screen.getByRole("button", { name: /CLEAR CONFIRMATION/i }));

    await waitFor(() => expect(toast).toHaveBeenCalled());
    const [message] = toast.mock.calls.at(-1);
    expect(message).toMatch(/next scan/i);
    expect(message).toMatch(/^1 file /);
  });

  it("refetches the list after clearing", async () => {
    /* Without this the cleared files stay on screen until something else
     * remounts the section, so the action looks like it did nothing — the
     * exact failure this whole section exists to fix, reintroduced one
     * level up. Dropping the reload survived mutation until this existed. */
    mockApi();
    setup();
    const user = userEvent.setup();

    await screen.findByText("A.mkv");
    const before = global.fetch.mock.calls.filter(
      ([u, o]) => String(u).includes("/acknowledged") && !o?.method).length;

    await user.click(screen.getAllByRole("checkbox")[0]);
    await user.click(screen.getByRole("button", { name: /CLEAR CONFIRMATION/i }));

    await waitFor(() => {
      const after = global.fetch.mock.calls.filter(
        ([u, o]) => String(u).includes("/acknowledged") && !o?.method).length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it("says so when clearing fails rather than looking successful", async () => {
    mockApi({ clearOk: false });
    const { toast } = setup();
    const user = userEvent.setup();

    await screen.findByText("A.mkv");
    await user.click(screen.getAllByRole("checkbox")[0]);
    await user.click(screen.getByRole("button", { name: /CLEAR CONFIRMATION/i }));

    await waitFor(() => expect(toast).toHaveBeenCalled());
    const [message, tone] = toast.mock.calls.at(-1);
    expect(message).toMatch(/could not clear/i);
    expect(tone).toBe("error");
  });

  it("says when it is showing only part of the list", async () => {
    /* The endpoint is paginated and this fetches one page, while the heading
     * shows the real total. Without this line the two disagree and the page
     * looks broken rather than truncated. */
    mockApi({ files: FILES, total: 57 });
    setup();

    expect(await screen.findByText(/Showing 2 of 57/i)).toBeTruthy();
  });

  it("does not claim truncation when the whole list is shown", async () => {
    mockApi({ files: FILES, total: 2 });
    setup();

    await screen.findByText("A.mkv");
    expect(screen.queryByText(/Showing \d+ of/i)).toBeNull();
  });
});


describe("across reloads", () => {
  /* refreshKey is the Review page's reviewRefreshKey, so this list reloads
   * after every finished job, queued file and finished scan. Each reload
   * used to untick everything and blank the rows to "Loading…".
   *
   * A second mock rather than a change to mockApi: these need the list to
   * change between fetches, or one fetch held open, and every existing test
   * wants the same list each time. */
  let release;
  const mockLists = (lists, { holdFetch = 0 } = {}) => {
    let gets = 0;
    release = null;
    global.fetch = vi.fn(async () => {
      const files = lists[Math.min(gets, lists.length - 1)];
      gets += 1;
      const reply = { ok: true, json: async () => ({ total: files.length, files }) };
      if (gets === holdFetch) return new Promise(resolve => { release = () => resolve(reply); });
      return reply;
    });
  };
  const renderAt = () => {
    const view = key => <AcknowledgedSection api={API} toast={vi.fn()} refreshKey={key} />;
    const { rerender } = render(view(0));
    return { reload: key => rerender(view(key)) };
  };
  const listFetches = () => global.fetch.mock.calls.length;
  const ticked = () => screen.getAllByRole("checkbox").map(box => box.checked);
  const button = () => screen.getByRole("button", { name: /CLEAR CONFIRMATION/i });

  it("keeps the ticked files when a finished job reloads the list", async () => {
    mockLists([FILES]);
    const { reload } = renderAt();
    const user = userEvent.setup();
    await screen.findByText("A.mkv");
    await user.click(screen.getAllByRole("checkbox")[0]);

    reload(1);
    await waitFor(() => expect(listFetches()).toBe(2));

    await waitFor(() => expect(ticked()).toEqual([true, false]));
    expect(button().textContent).toContain("CLEAR CONFIRMATION (1 file)");
  });

  it("unticks a file that leaves the list, so it does not come back ticked", async () => {
    /* File ids recur. B cleared in another tab and confirmed again later is
     * the same id; kept ticked while it was gone, it would come back ticked
     * and be one click from being cleared again unseen. */
    mockLists([FILES, [FILES[0]], FILES]);
    const { reload } = renderAt();
    const user = userEvent.setup();
    await screen.findByText("B.mkv");
    await user.click(screen.getAllByRole("checkbox")[1]);

    reload(1);
    await waitFor(() => expect(screen.queryByText("B.mkv")).toBeNull());
    expect(button().textContent).toContain("(0 files)");
    expect(button().disabled).toBe(true);

    reload(2);
    await screen.findByText("B.mkv");
    expect(ticked()).toEqual([false, false]);
  });

  it("says Loading… before the first list arrives", async () => {
    /* The other half of the rule the reload test pins: with nothing to
     * show yet, an empty section with a disabled button would look like a
     * list with nothing in it. */
    mockLists([FILES], { holdFetch: 1 });
    renderAt();
    await waitFor(() => expect(release).toBeTypeOf("function"));

    expect(screen.getByText("Loading…")).toBeTruthy();
    release();
    await screen.findByText("A.mkv");
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("keeps the rows on screen while it reloads", async () => {
    mockLists([FILES], { holdFetch: 2 });
    const { reload } = renderAt();
    await screen.findByText("A.mkv");

    reload(1);
    await waitFor(() => expect(release).toBeTypeOf("function"));

    expect(screen.getByText("A.mkv")).toBeTruthy();
    expect(screen.queryByText("Loading…")).toBeNull();
    release();
    await waitFor(() => expect(listFetches()).toBe(2));
  });
});
