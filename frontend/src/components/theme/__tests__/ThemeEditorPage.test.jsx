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
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  ThemeContext, themes,
  buildStatusColor, buildLevelColor, buildToastTone, buildActionCfg,
} from "../../../theme";
import { themeToInputs, THEME_KEY_ORDER } from "../../../themeSource";
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
      fontSize: t.type.size.h1,
      letterSpacing: t.type.tracking.wide,
      padding: t.space.md,
      fontFamily: t.type.root,
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

/* Only PALETTE is open on mount — 117 controls in one column is unusable
 * otherwise. Anything reaching another group has to expand it first, so this
 * clicks every collapsed header. Driven off aria-expanded rather than a list
 * of group names, which would be the same hardcoded-list mistake the
 * component itself avoids. */
const openEveryGroup = async (user) => {
  for (const header of screen.getAllByRole("button", { expanded: false })) {
    await user.click(header);
  }
};

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

    const field = screen.getByLabelText("palette.bg");
    await user.clear(field);
    await user.type(field, "#123456");

    expect(screen.getByTestId("probe").style.background).toBe("rgb(18, 52, 86)");
  });

  it("carries an edited colour into the maps derived from it", async () => {
    // The point of editing inputs rather than outputs. A green change has to
    // reach statusColor.success, which no control touches directly.
    const user = userEvent.setup();
    renderEditor("terminal");

    const field = screen.getByLabelText("palette.green");
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

    const field = screen.getByLabelText("palette.amber");
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

    const field = screen.getByLabelText("palette.bg");
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

    const field = screen.getByLabelText("palette.bg");
    await user.clear(field);
    await user.type(field, themes.terminal.palette.text);

    // The preview follows the draft into the unreadable state...
    expect(screen.getByTestId("probe").style.background)
    .toBe(hexToRgb(themes.terminal.palette.text));

    // ...and the control that caused it is still drawn against the ACTIVE
    // theme's background, so it can be undone.
    expect(screen.getByLabelText("palette.bg").style.background)
    .toBe(hexToRgb(themes.terminal.palette.bg));
  });
});

describe("ThemeEditorPage — base theme selection", () => {
  it("reloads the draft from the theme picked, discarding edits", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");

    const field = screen.getByLabelText("palette.bg");
    await user.clear(field);
    await user.type(field, "#123456");
    expect(screen.getByTestId("probe").style.background).toBe("rgb(18, 52, 86)");

    await user.click(screen.getByRole("button", { name: "SOFT" }));

    expect(screen.getByTestId("probe").style.background)
    .toBe(hexToRgb(themes.soft.palette.bg));
    expect(screen.getByLabelText("palette.bg")).toHaveValue(themes.soft.palette.bg);
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

    const field = screen.getByLabelText("palette.bg");
    await user.clear(field);
    await user.type(field, "#abc");

    expect(screen.getByLabelText("palette.bg swatch")).toBeDisabled();
    expect(field).toHaveValue("#abc");
  });

  it("enables the swatch again once the value is representable", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");

    const field = screen.getByLabelText("palette.bg");
    await user.clear(field);
    await user.type(field, "#abc");
    expect(screen.getByLabelText("palette.bg swatch")).toBeDisabled();

    await user.type(field, "def");
    expect(screen.getByLabelText("palette.bg swatch")).toBeEnabled();
    expect(screen.getByLabelText("palette.bg swatch")).toHaveValue("#abcdef");
  });

  it("offers a control for every palette key", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);
    for (const key of Object.keys(themes.terminal.palette)) {
      expect(screen.getByLabelText(`palette.${key}`)).toBeInTheDocument();
    }
  });
});

describe("ThemeEditorPage — coverage of every input", () => {
  /* The one assertion that makes the derived-control approach worth
   * anything. Fields are generated by walking the draft's inputs, and this
   * walks the same inputs independently and demands a control for each leaf.
   *
   * A hardcoded field list would fail here the moment theme.jsx gained a
   * token, which is the point: without this, a group added to a theme simply
   * has no control, the editor writes whatever the base theme carried for
   * it, and the only symptom is a value nobody can change. */
  const leafPaths = (value, path = []) =>
  value && typeof value === "object"
  ? Object.entries(value).flatMap(([k, v]) => leafPaths(v, [...path, k]))
  : [path.join(".")];

  it("offers a control for all 117 editable inputs", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    const paths = leafPaths(themeToInputs(themes.terminal));

    // Counted, not estimated: 4 identity, 13 palette, 14 tint, 15 surface,
    // 31 type, 5 radius, 14 space, 21 size.
    expect(paths).toHaveLength(117);

    const missing = paths.filter((p) => screen.queryByLabelText(p) === null);
    expect(missing).toEqual([]);
  });

  it("writes to the token the control is named after", async () => {
    // The path is both the accessible name and the argument to setAt, so
    // this pins that they cannot drift: editing type.size.h1 must move
    // type.size.h1 in the preview and nothing else.
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    const before = themes.terminal.type.size.xs;
    const field = screen.getByLabelText("type.size.h1");
    await user.clear(field);
    await user.type(field, "40");

    const probe = screen.getByTestId("probe");
    expect(probe.style.fontSize).toBe("40px");
    expect(probe.style.letterSpacing).toBe(themes.terminal.type.tracking.wide);
    expect(before).toBe(themes.terminal.type.size.xs);
  });

  it("keeps a number a number and a string a string", async () => {
    /* radius mixes 999 with "50%", and the serialiser refuses a type change
     * at save. A control that coerced everything to string would produce a
     * theme that still rendered, since CSS accepts "999", and would fail
     * only much later at the point of writing the file. */
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    const num = screen.getByLabelText("space.md");
    await user.clear(num);
    await user.type(num, "18");
    expect(screen.getByTestId("probe").style.padding).toBe("18px");

    expect(screen.getByLabelText("radius.full")).toHaveValue("50%");
    expect(screen.getByLabelText("radius.full").getAttribute("type")).toBe("text");
    expect(screen.getByLabelText("radius.pill").getAttribute("type")).toBe("number");
  });

  it("does not turn a cleared number field into a silent zero", async () => {
    /* Number("") is 0, so coercing every keystroke makes an empty box read
     * as a real value. space.md becoming 0 collapses padding everywhere the
     * token is used and looks like a layout regression rather than an empty
     * input, and it would be written to the saved theme as a legitimate 0.
     *
     * Leaving the raw string means the preview goes visibly wrong and the
     * serialiser refuses at save, naming the path. */
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    await user.clear(screen.getByLabelText("space.md"));

    expect(screen.getByTestId("probe").style.padding).not.toBe("0px");
    expect(screen.getByTestId("probe").style.padding).toBe("");
  });

  it("gives a shadow a text field, not a colour picker", async () => {
    /* surface.drawerShadow is "0 4px 16px #00000066". A colour picker here
     * would replace the whole declaration with a bare hex on first use,
     * losing the offsets and the blur. */
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    expect(screen.getByLabelText("surface.drawerShadow"))
    .toHaveValue(themes.terminal.surface.drawerShadow);
    expect(screen.queryByLabelText("surface.drawerShadow swatch")).toBeNull();
    expect(screen.getByLabelText("surface.rowHoverBg swatch")).toBeInTheDocument();
  });

  it("disables the swatch for the alpha and short hex forms surface uses", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    // #ffffff07 — 8-digit, carries alpha the 6-digit picker cannot hold.
    expect(screen.getByLabelText("surface.rowHoverBg swatch")).toBeDisabled();
    // #111 — 3-digit.
    expect(screen.getByLabelText("surface.badgeFallbackBg swatch")).toBeDisabled();
    // rgba().
    expect(screen.getByLabelText("surface.guardScrimBg swatch")).toBeDisabled();
    // A plain 6-digit value still gets a working swatch.
    expect(screen.getByLabelText("surface.logBg swatch")).toBeEnabled();
  });

  it("offers dark and light for colorScheme rather than free text", async () => {
    /* ThemeProvider writes this into ":root { color-scheme: ... }". CSS
     * drops a declaration it cannot parse, so a typo stops the browser
     * theming form controls and scrollbars with nothing to say why. */
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    const select = screen.getByLabelText("colorScheme");
    expect(select.tagName).toBe("SELECT");
    expect([...select.options].map((o) => o.value)).toEqual(["dark", "light"]);
  });
});

describe("ThemeEditorPage — font stacks", () => {
  it("warns when a stack has no generic fallback", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    // Both shipped stacks end in a generic keyword, so neither warns.
    expect(screen.queryByTestId("type.root warning")).toBeNull();
    expect(screen.queryByTestId("type.mono warning")).toBeNull();

    const field = screen.getByLabelText("type.root");
    await user.clear(field);
    await user.type(field, "'Comic Sans MS'");

    expect(screen.getByTestId("type.root warning")).toBeInTheDocument();
  });

  it("clears the warning once a generic fallback is added", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    const field = screen.getByLabelText("type.root");
    await user.clear(field);
    await user.type(field, "'Comic Sans MS'");
    expect(screen.getByTestId("type.root warning")).toBeInTheDocument();

    await user.type(field, ", monospace");
    expect(screen.queryByTestId("type.root warning")).toBeNull();
  });

  it("renders the sample in the stack being edited", async () => {
    /* The sample is the only thing that can show a family failed to load. A
     * static check cannot; a stack that fell through to a system face looks
     * nothing like the bundled ones. */
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    // Asserted by distinctive family rather than by exact string: jsdom
    // normalises CSS font-family quoting from ' to ", so the serialised
    // value never equals the theme's literal even when it is correct.
    expect(screen.getByTestId("type.root sample").style.fontFamily)
    .toContain("JetBrains Mono Variable");

    await user.click(screen.getByRole("button", { name: "type.root INTER" }));

    expect(screen.getByTestId("type.root sample").style.fontFamily)
    .toContain("Inter Variable");
    expect(screen.getByLabelText("type.root")).toHaveValue(
      "'Inter Variable', 'Inter', system-ui, sans-serif",
    );
  });
});

describe("ThemeEditorPage — export", () => {
  /* Evaluate an emitted block the way theme.jsx would, by supplying the four
   * builders it calls and handing back the const it declares. Matching the
   * text against an expected string would pin formatting, which is not what
   * has to be right, and would pass on a block that is not valid JavaScript. */
  const evaluateBlock = (src, id) =>
  new Function(
    "buildStatusColor", "buildLevelColor", "buildToastTone", "buildActionCfg",
    `${src}\nreturn ${id};`,
  )(buildStatusColor, buildLevelColor, buildToastTone, buildActionCfg);

  it("exports a block that evaluates to exactly what the preview is showing", async () => {
    /* The assertion that closes the loop. Everything else checks the preview
     * or the serialiser in isolation; this checks they agree. A theme that
     * looks right on screen and exports as something else is the failure
     * this whole feature would be worth nothing without, and it would show
     * up only after the file was pasted in and the app reloaded. */
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    await user.clear(screen.getByLabelText("palette.green"));
    await user.type(screen.getByLabelText("palette.green"), "#00ff88");
    await user.clear(screen.getByLabelText("space.md"));
    await user.type(screen.getByLabelText("space.md"), "18");

    const probe = screen.getByTestId("probe");
    expect(probe.style.borderColor).toBe("rgb(0, 255, 136)");
    expect(probe.style.padding).toBe("18px");

    const exported = evaluateBlock(
      screen.getByTestId("export-source").value, "terminal",
    );
    expect(exported.palette.green).toBe("#00ff88");
    expect(exported.space.md).toBe(18);
    // Regenerated from the edited palette, not carried over from the base.
    expect(exported.statusColor.success).toBe("#00ff88");
    expect(exported.actionCfg.copy_track.text).toBe("#00ff88");
  });

  it("exports every one of the 14 top-level keys", async () => {
    const user = userEvent.setup();
    renderEditor("soft");
    await openEveryGroup(user);

    const exported = evaluateBlock(
      screen.getByTestId("export-source").value, "soft",
    );
    expect(Object.keys(exported)).toEqual(THEME_KEY_ORDER);
    expect(JSON.stringify(exported)).toBe(JSON.stringify(themes.soft));
  });

  it("shows the serialiser's own refusal instead of an export", async () => {
    /* No second validator in the component. The message is the one
     * themeSource.js raises, naming the token path, so the UI cannot say a
     * theme is fine while the file it would write is not. */
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    await user.clear(screen.getByLabelText("space.md"));

    expect(screen.getByTestId("export-error")).toHaveTextContent("space.md");
    expect(screen.queryByTestId("export-source")).toBeNull();
    expect(screen.queryByRole("button", { name: /^DOWNLOAD/ })).toBeNull();
  });

  it("refuses an id that would not survive as a const declaration", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    const field = screen.getByLabelText("id");
    await user.clear(field);
    await user.type(field, "my-theme");

    expect(screen.getByTestId("export-error")).toHaveTextContent("my-theme");
    expect(screen.queryByTestId("export-source")).toBeNull();
  });

  it("recovers once the draft is valid again", async () => {
    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    await user.clear(screen.getByLabelText("space.md"));
    expect(screen.getByTestId("export-error")).toBeInTheDocument();

    await user.type(screen.getByLabelText("space.md"), "10");
    expect(screen.queryByTestId("export-error")).toBeNull();
    expect(screen.getByTestId("export-source")).toBeInTheDocument();
  });

  it("downloads the block under the theme's own id", async () => {
    const created = [];
    vi.stubGlobal("URL", {
      ...globalThis.URL,
      createObjectURL: (blob) => { created.push(blob); return "blob:stub"; },
                  revokeObjectURL: () => {},
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(function () { clicked = this; });
    let clicked = null;

    const user = userEvent.setup();
    renderEditor("terminal");
    await openEveryGroup(user);

    const field = screen.getByLabelText("id");
    await user.clear(field);
    await user.type(field, "aurora");

    await user.click(screen.getByRole("button", { name: /^DOWNLOAD/ }));

    expect(click).toHaveBeenCalledTimes(1);
    expect(clicked.download).toBe("aurora.theme.js");
    expect(created).toHaveLength(1);
    // Read through FileReader: jsdom's Blob implements neither text() nor
    // arrayBuffer(), so the usual await blob.text() silently is not a
    // function rather than returning the bytes.
    const text = await new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result);
      r.onerror = () => reject(r.error);
      r.readAsText(created[0]);
    });
    // What is downloaded is byte-identical to what is on screen.
    expect(text).toBe(screen.getByTestId("export-source").value);
  });
});
