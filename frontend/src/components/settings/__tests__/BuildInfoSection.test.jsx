/**
 * Build info under Maintenance & Logs.
 *
 * The app reported a hardcoded version for the life of the project and
 * showed it nowhere, so the bug report template asked for something no user
 * could find. This is the place they find it, sitting beside the log viewer
 * because the same template asks for both.
 *
 * Two behaviours here are easy to get backwards and both matter to whoever
 * reads the resulting bug report:
 *
 *   • Copy writes the FULL sha, not the seven displayed. A report carrying
 *     seven characters is usually enough and occasionally ambiguous, and the
 *     failure is silent at the point it matters.
 *   • The copy button is hidden when the clipboard API is missing, which is
 *     the norm over plain HTTP on a LAN address — how most people reach this
 *     app. A button that silently does nothing is worse than no button, so
 *     the value stays rendered as selectable text either way.
 *
 * Renders null on failure rather than showing an error. A version line is
 * not worth an error state on a settings page: if health is unreachable the
 * user has a larger problem and the rest of the page will already say so.
 *
 * Verified by mutation, 5 applied, 5 killed:
 *
 *   • Copy writing commit_short instead of the full sha   → killed
 *   • Button rendered when clipboard is absent            → killed
 *   • Short commit rendered as the full sha               → killed
 *   • Failure rendering an error instead of null          → killed
 *   • version omitted from the displayed label            → killed
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BuildInfoSection } from "../BuildInfoSection";
import { ThemeProvider } from "../../../theme";

const FULL = "94307cc1f2a3b4c5d6e7f8091a2b3c4d5e6f7081";

const HEALTH = {
  status: "ok",
  app: "Remuxarr",
  version: "v1.4.0",
  commit: FULL,
  commit_short: "94307cc",
};

const renderSection = () =>
  render(
    <ThemeProvider>
    <BuildInfoSection api="" />
    </ThemeProvider>
  );

beforeEach(() => {
  global.fetch = vi.fn(() =>
    Promise.resolve({ ok: true, json: () => Promise.resolve(HEALTH) })
  );
});

afterEach(() => {
  vi.restoreAllMocks();
  delete navigator.clipboard;
});

describe("BuildInfoSection", () => {
  it("shows the version and the short commit", async () => {
    renderSection();

    const code = await screen.findByText(/v1\.4\.0/);
    expect(code.textContent).toBe("v1.4.0 (94307cc)");
  });

  it("keeps the full sha available on the element title", async () => {
    renderSection();

    const code = await screen.findByText(/v1\.4\.0/);
    expect(code.getAttribute("title")).toBe(FULL);
  });

  it("copies the full sha, not the seven shown", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    navigator.clipboard = { writeText };

    renderSection();
    const btn = await screen.findByRole("button", { name: /copy/i });
    await userEvent.click(btn);

    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const written = writeText.mock.calls[0][0];
    expect(written).toContain(FULL);
    expect(written).toBe(`v1.4.0 ${FULL}`);
  });

  it("hides the copy button when the clipboard API is unavailable", async () => {
    renderSection();

    await screen.findByText(/v1\.4\.0/);
    expect(screen.queryByRole("button", { name: /copy/i })).toBeNull();
  });

  it("renders nothing when health cannot be reached", async () => {
    global.fetch = vi.fn(() => Promise.reject(new Error("network down")));

    const { container } = renderSection();

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("renders nothing when health returns a non-ok status", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) })
    );

    const { container } = renderSection();

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });
});
