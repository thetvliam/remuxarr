/**
 * SettingsPage — what happens when the initial load does not succeed.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * loadSettings parsed both responses without looking at the status code:
 *
 *   fetch(`${api}/api/settings/schema`).then(r => r.json())
 *
 * `fetch` rejects on a network failure, not on an HTTP error, and a FastAPI
 * error carries a {"detail": ...} body that json() parses happily. So an
 * error response resolved down the SUCCESS path, and the `loadError` state
 * that exists for exactly this case was reachable only by the narrower
 * failure — an unreachable host, or a body that would not parse.
 *
 * The consequence was not a wrong value on screen. `setSchema` stored the
 * error object, and `schema.map` (dirtyKeys) then ran against something with
 * no .map. There is no error boundary anywhere in the tree, so that throw
 * unmounts the root: the whole app goes blank, not just this page.
 *
 * These tests therefore assert the placeholder the page already knows how to
 * render. Asserting "did not crash" would pass on a page that rendered
 * nothing at all, which is the other way this can go wrong.
 *
 * A successful response of the wrong shape got through the same way, past
 * the status check: a schema that was not a list blanked the app exactly as
 * above, and values that were not an object rendered every field at a
 * default. Both now go down the load-error path too. Verified by mutation:
 * removing either shape check survived the suite without the two tests
 * below that cover them, and each is killed by its own.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPage } from "../SettingsPage";
import { ThemeProvider } from "../../../theme";

const SCHEMA = [
  { key: "und_audio_threshold", label: "Undefined audio threshold", type: "integer", min: 1, group: "Worker" },
];

const VALUES = { und_audio_threshold: 2 };

/**
 * schemaOk / valuesOk pick which of the two requests fails, because they fail
 * differently and only one of them crashes. A bad schema takes the whole app
 * down; bad values render every field blank and let the user save over
 * settings they never saw. Both are load failures and both belong in the
 * placeholder, so both are pinned.
 *
 * The error body is FastAPI's real shape. A body that would not parse is a
 * different and already-handled path — see the network-failure test below.
 */
function mockApi({ schemaOk = true, valuesOk = true, status = 500,
                   schema = SCHEMA, values = VALUES } = {}) {
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    const u = String(url);
    if (u.includes("/api/settings/schema")) {
      return schemaOk
        ? { ok: true, status: 200, json: async () => schema }
        : { ok: false, status, json: async () => ({ detail: "Internal Server Error" }) };
    }
    if (u.match(/\/api\/settings\/?$/)) {
      return valuesOk
        ? { ok: true, status: 200, json: async () => values }
        : { ok: false, status, json: async () => ({ detail: "Internal Server Error" }) };
    }
    // Status pollers used by the page's sub-sections.
    return { ok: true, status: 200, json: async () => ({ value: null, count: 0 }) };
  }));
}

function renderPage() {
  // Worker is an ordinary field-rendering category, so the placeholder this
  // file asserts on is the one a failed load actually produces. The custom
  // categories (maintenance, backup, appearance) render their own sections
  // and never consult `schema` at all.
  localStorage.setItem("remuxarr.settingsCategory", "worker");
  render(
    <ThemeProvider>
      <SettingsPage api="" toast={() => {}} />
    </ThemeProvider>,
  );
}

const LOAD_ERROR = /Couldn't load settings from the backend/i;

let errorSpy;
beforeEach(() => {
  // The load failure is deliberately reported once — the source comment
  // contrasts it with the pollers, which swallow because they retry. Spying
  // keeps the suite output clean and pins that the report still happens.
  errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => errorSpy.mockRestore());

describe("SettingsPage — failed initial load", () => {
  it("shows the load-error placeholder when the schema request errors", async () => {
    mockApi({ schemaOk: false });
    renderPage();

    expect(await screen.findByText(LOAD_ERROR)).toBeInTheDocument();
  });

  it("shows the load-error placeholder when the values request errors", async () => {
    /* The schema arrives fine here, so without a status check the page has
     * every field it needs and renders them — against values that are really
     * an error body. Every control shows a default it was never given, and
     * saving writes those defaults back. */
    mockApi({ valuesOk: false });
    renderPage();

    expect(await screen.findByText(LOAD_ERROR)).toBeInTheDocument();
    expect(screen.queryByLabelText("Undefined audio threshold")).not.toBeInTheDocument();
  });

  it("shows the load-error placeholder when the schema arrives but is not a list", async () => {
    /* A 200 of the wrong shape. The status check lets it through, and the
     * page stored it as it came: schema.map then threw during render and,
     * with no error boundary, blanked the whole app. A working server
     * always sends a list; this is for one that does not. */
    mockApi({ schema: {} });
    renderPage();

    expect(await screen.findByText(LOAD_ERROR)).toBeInTheDocument();
  });

  it("shows the load-error placeholder when the values arrive but are not an object", async () => {
    /* The quieter half: the schema is fine, so every field rendered, each at
     * a default it was never given, and saving would write those back. */
    mockApi({ values: [] });
    renderPage();

    expect(await screen.findByText(LOAD_ERROR)).toBeInTheDocument();
    expect(screen.queryByLabelText("Undefined audio threshold")).not.toBeInTheDocument();
  });

  it("reports the failure once rather than swallowing it", async () => {
    mockApi({ schemaOk: false });
    renderPage();

    await screen.findByText(LOAD_ERROR);
    expect(errorSpy).toHaveBeenCalled();
  });

  it("still handles a rejected request, which already worked", async () => {
    /* The path that was always covered. It is here so that routing HTTP
     * errors into the same branch cannot be done by moving the network
     * failure out of it. */
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    renderPage();

    expect(await screen.findByText(LOAD_ERROR)).toBeInTheDocument();
  });

  it("renders the fields normally when both requests succeed", async () => {
    /* The guard must not make a healthy load look like a failure. */
    mockApi();
    renderPage();

    expect(await screen.findByLabelText("Undefined audio threshold")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(LOAD_ERROR)).not.toBeInTheDocument());
  });
});
