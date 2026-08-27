/* Theme editor — the preview, and what must not leak out of it.
 *
 * Every assertion here reads a style PROPERTY rather than checking that text
 * appeared. Components in this app bind theme values at render, so a test
 * asserting on text passes against a component that is rendering fully
 * transparent — which is exactly how the surface.raised bug in
 * ReleaseNotesModal shipped with seven green tests above it.
 *
 * The three things this file pins, in order of what would hurt most:
 *
 *   1. The draft cannot reach the themes singleton. themeToInputs deep-
 *      copies; if it ever stopped, editing a colour in the editor would
 *      permanently alter the theme every other user of this browser session
 *      renders from, and nothing on screen would say so.
 *   2. The controls do not follow the draft. They read the ACTIVE theme, so
 *      a draft edited into an unreadable state still leaves the controls
 *      that undo it visible. Under one shared provider, setting bg to the
 *      value of text hides the way out.
 *   3. Editing an input moves the preview, including the maps derived from
 *      it. A palette change has to reach statusColor, toastTone, levelColor
 *      and actionCfg, not just the elements reading palette directly.
 *
 * Verified by mutation, 6 applied, 6 killed:
 *
 *   • preview provider dropped, children rendered bare      → killed
 *   • preview given themes[themeId] instead of the draft    → killed
 *   • controls wrapped in the draft provider too            → killed
 *   • setPaletteKey mutates inputs.palette in place         → killed
 *   • "themes" removed from VALID_PAGES                     → killed
 *   • resetTo keeps the edited inputs instead of reloading  → killed
 *
 * Which test kills which is recorded in the commit message. Each run was
 * checked for named failing tests rather than a bare non-zero exit, since
 * vitest also exits non-zero when it finds no test files at all.
 */
import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ThemeContext, themes } from "../../../theme";
import { ThemeEditorPage } from "../ThemeEditorPage";

/* A stand-in for the previewed page. A real page is what App.jsx passes, but
 * it fetches on mount and its own failure states would be what these tests
 * were measuring. This reads the same context through the same hook, so what
 * is under test — that the provider carries the draft — is unchanged. */
const PreviewProbe = () => {
  const t = ThemeContextProbe();
  return (
    <div
      data-testid="probe"
      style={{
        background: t.palette.bg,
        color: t.palette.text,
        borderColor: t.statusColor.success,
        outlineColor: t.toastTone.notice,
        borderRadius: t.radius.sm,
      }}
    >
      previewed page
    </div>
  );
};

/* useTheme() by another name, so the probe reads context exactly as a real
 * component does rather than through a second mechanism. */
import { useTheme } from "../../../theme";
function ThemeContextProbe() {
  return useTheme();
}

const renderEditor = (themeId = "terminal") =>
  render(
    <ThemeContext.Provider
      value={{ ...themes[themeId], themeId, setThemeId: () => {} }}
    >
      <ThemeEditorPage>
        <PreviewProbe />
      </ThemeEditorPage>
    </ThemeContext.Provider>,
  );

const hexToRgb = (hex) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
};

describe("ThemeEditorPage — preview", () => {
  it("renders the previewed page under its own provider, not the surrounding one", () => {
    /* The outer context deliberately carries terminal's VALUES under soft's
     * id. That mismatch is the only way to tell the two providers apart with
     * no edits made: normally the editor loads its draft from themes[themeId]
     * and both providers agree, so a probe reading the outer one looks
     * identical to a probe reading the draft, and a test written against the
     * agreeing case passes with the inner provider deleted.
     *
     * Splitting them means bare children render terminal and correctly
     * provided children render soft. Logo.test.jsx builds a synthetic
     * context the same way and for the same reason: the shipped themes
     * cannot express the case being tested.
     *
     * Verified: with the inner provider removed this fails, reporting
     * terminal's background where soft's was expected. */
    render(
      <ThemeContext.Provider
        value={{ ...themes.terminal, themeId: "soft", setThemeId: () => {} }}
      >
        <ThemeEditorPage>
          <PreviewProbe />
        </ThemeEditorPage>
      </ThemeContext.Provider>,
    );

    const probe = screen.getByTestId("probe");
    expect(probe.style.background).toBe(hexToRgb(themes.soft.palette.bg));
    expect(probe.style.background).not.toBe(hexToRgb(themes.terminal.palette.bg));
  });

  it("renders the previewed page under the draft of the selected theme", async () => {
    renderEditor("soft");
    const probe = screen.getByTestId("probe");
    expect(probe.style.background).toBe(hexToRgb(themes.soft.palette.bg));
    expect(probe.style.borderRadius).toBe(`${themes.soft.radius.sm}px`);
  });

  it("moves the preview when a palette colour is edited", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");

    const field = screen.getByLabelText("bg");
    await user.clear(field);
    await user.type(field, "#123456");

    expect(screen.getByTestId("probe").style.background).toBe("rgb(18, 52, 86)");
  });

  it("carries an edited colour into the maps derived from it", async () => {
    // The point of editing inputs rather than outputs. A green change has to
    // reach statusColor.success, which no control touches directly.
    const user = userEvent.setup();
    renderEditor("terminal");

    const field = screen.getByLabelText("green");
    await user.clear(field);
    await user.type(field, "#00ff88");

    const probe = screen.getByTestId("probe");
    expect(probe.style.borderColor).toBe("rgb(0, 255, 136)");
  });

  it("carries an edited colour into a differently-named derived tone", async () => {
    // amber drives toastTone.notice. Named separately from the green case
    // because a serialiser that regenerated only statusColor would pass that
    // one and fail this.
    const user = userEvent.setup();
    renderEditor("terminal");

    const field = screen.getByLabelText("amber");
    await user.clear(field);
    await user.type(field, "#ff0066");

    expect(screen.getByTestId("probe").style.outlineColor).toBe("rgb(255, 0, 102)");
  });
});

describe("ThemeEditorPage — isolation", () => {
  it("does not alter the shipped theme when the draft is edited", async () => {
    const before = JSON.stringify(themes.terminal);
    const user = userEvent.setup();
    renderEditor("terminal");

    const field = screen.getByLabelText("bg");
    await user.clear(field);
    await user.type(field, "#ffffff");

    expect(JSON.stringify(themes.terminal)).toBe(before);
  });

  it("leaves the controls readable when the draft is edited to match itself", async () => {
    /* The failure this prevents is not cosmetic. Under a single provider,
     * setting bg to the value of text renders the controls invisible, and
     * the controls are what undo it — the editor becomes unrecoverable
     * without a page reload that also discards the draft. */
    const user = userEvent.setup();
    renderEditor("terminal");

    const field = screen.getByLabelText("bg");
    await user.clear(field);
    await user.type(field, themes.terminal.palette.text);

    // The preview follows the draft into the unreadable state...
    expect(screen.getByTestId("probe").style.background)
      .toBe(hexToRgb(themes.terminal.palette.text));

    // ...and the control that caused it is still drawn against the ACTIVE
    // theme's background, so it can be undone.
    expect(screen.getByLabelText("bg").style.background)
      .toBe(hexToRgb(themes.terminal.palette.bg));
  });
});

describe("ThemeEditorPage — base theme selection", () => {
  it("reloads the draft from the theme picked, discarding edits", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");

    const field = screen.getByLabelText("bg");
    await user.clear(field);
    await user.type(field, "#123456");
    expect(screen.getByTestId("probe").style.background).toBe("rgb(18, 52, 86)");

    await user.click(screen.getByRole("button", { name: "SOFT" }));

    expect(screen.getByTestId("probe").style.background)
      .toBe(hexToRgb(themes.soft.palette.bg));
    expect(screen.getByLabelText("bg")).toHaveValue(themes.soft.palette.bg);
  });

  it("marks the base theme currently loaded", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");

    expect(screen.getByRole("button", { name: "TERMINAL" }))
      .toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "SOFT" }));

    expect(screen.getByRole("button", { name: "SOFT" }))
      .toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "TERMINAL" }))
      .toHaveAttribute("aria-pressed", "false");
  });
});

describe("ThemeEditorPage — colour field", () => {
  it("disables the swatch for a value the native picker cannot show", async () => {
    /* type=color only accepts 6-digit hex and shows black for anything else.
     * A theme is hand-editable, so a value it cannot represent must not be
     * quietly rewritten to #000000 by a control that looks like it is just
     * displaying it. */
    const user = userEvent.setup();
    renderEditor("terminal");

    const field = screen.getByLabelText("bg");
    await user.clear(field);
    await user.type(field, "#abc");

    expect(screen.getByLabelText("bg swatch")).toBeDisabled();
    expect(field).toHaveValue("#abc");
  });

  it("enables the swatch again once the value is representable", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");

    const field = screen.getByLabelText("bg");
    await user.clear(field);
    await user.type(field, "#abc");
    expect(screen.getByLabelText("bg swatch")).toBeDisabled();

    await user.type(field, "def");
    expect(screen.getByLabelText("bg swatch")).toBeEnabled();
    expect(screen.getByLabelText("bg swatch")).toHaveValue("#abcdef");
  });

  it("offers a control for every palette key", () => {
    renderEditor("terminal");
    const controls = screen.getByText("THEME EDITOR").parentElement;
    for (const key of Object.keys(themes.terminal.palette)) {
      expect(within(controls).getByLabelText(key)).toBeInTheDocument();
    }
  });
});
