/**
 * The header logo — which asset renders under which colour scheme.
 *
 * The mark is theme-neutral (orange artwork on a transparent ground) so it
 * never varies. The WORDMARK does: its lettering is near-white in the dark
 * asset and near-black in the light one, so picking the wrong file makes it
 * invisible rather than merely off-brand. Nothing else in the app would
 * surface that — the <img> loads successfully either way, so the component's
 * own onError fallback never fires and the header looks empty where the
 * lockup should be.
 *
 * Keyed on the ACTIVE THEME's colorScheme rather than the system's
 * prefers-color-scheme, deliberately. Remuxarr picks its own palette, so
 * following the system would give a user on terminal with their OS set to
 * light black lettering on Remuxarr's dark header.
 *
 * The scheme cases were written when every shipped theme was dark and the
 * light branch was reachable only through a synthetic context — the contract
 * fixed ahead of anything exercising it. dawn, paper and linen have since
 * shipped, so the last case below runs the real ones and the pairing is no
 * longer taken on trust.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ThemeContext, themes } from "../../../theme";
import { AppHeader } from "../AppHeader";

/* isMobile is a prop, not a media query, and the two layouts render different
   logo variants — desktop the wordmark, mobile the icon-only mark. Both are
   exercised rather than assuming the default. */
function srcsForScheme(colorScheme, { isMobile = false } = {}) {
  const value = {
    ...themes.terminal,
    colorScheme,
    themeId: "terminal",
    setThemeId: () => {},
  };
  const { unmount } = render(
    <ThemeContext.Provider value={value}>
      <AppHeader page="dashboard" setPage={() => {}} wsConnected isMobile={isMobile} />
    </ThemeContext.Provider>,
  );
  const srcs = screen.getAllByAltText("Remuxarr").map((i) => i.getAttribute("src"));
  unmount();
  return srcs;
}

/** Relative luminance of a hex colour, per WCAG. Used so the shipped-theme
    case can judge "is this background light" from the colour itself rather
    than from the colorScheme field it exists to check. */
function luminance(hex) {
  const h = hex.replace("#", "");
  const n = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(n.slice(i, i + 2), 16) / 255);
  const f = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

/** Renders a real shipped theme, rather than terminal with its scheme
    overwritten. */
function srcsForTheme(theme) {
  const { unmount } = render(
    <ThemeContext.Provider value={{ ...theme, themeId: theme.id, setThemeId: () => {} }}>
      <AppHeader page="dashboard" setPage={() => {}} wsConnected isMobile={false} />
    </ThemeContext.Provider>,
  );
  const srcs = screen.getAllByAltText("Remuxarr").map((i) => i.getAttribute("src"));
  unmount();
  return srcs;
}

describe("header logo", () => {
  it("uses the light-ink wordmark on a dark theme", () => {
    const srcs = srcsForScheme("dark");

    expect(srcs).toContain("/logo-name-dark.svg");
    expect(srcs).not.toContain("/logo-name-light.svg");
  });

  it("uses the dark-ink wordmark on a light theme", () => {
    const srcs = srcsForScheme("light");

    expect(srcs).toContain("/logo-name-light.svg");
    expect(srcs).not.toContain("/logo-name-dark.svg");
  });

  it("uses the same theme-neutral mark on mobile under either scheme", () => {
    /* The mark is orange throughout on a transparent ground, so it needs no
       per-scheme variant — asserting it does NOT vary is the point. */
    expect(srcsForScheme("dark",  { isMobile: true })).toContain("/logo.svg");
    expect(srcsForScheme("light", { isMobile: true })).toContain("/logo.svg");
  });

  it("gives every shipped theme a wordmark legible on its own background", () => {
    /* The cases above pin the colorScheme -> asset rule against a synthetic
       context. This one checks that what ships actually lands on the right
       side of it, and deliberately does NOT read colorScheme to decide what
       to expect: doing that only asserts the component agrees with the
       field, which is true however the field is set. Measuring the theme's
       own background instead is what catches the failure worth catching —
       a theme added with a missing or misspelled colorScheme falls to the
       dark branch and is invisible on a light background. */
    for (const theme of Object.values(themes)) {
      const light = luminance(theme.palette.bg) > 0.5;
      const expected = light ? "/logo-name-light.svg" : "/logo-name-dark.svg";
      const wrong   = light ? "/logo-name-dark.svg"  : "/logo-name-light.svg";
      const srcs = srcsForTheme(theme);
      const where = `${theme.id} (bg ${theme.palette.bg}, declared ${theme.colorScheme})`;

      expect(srcs, where).toContain(expected);
      expect(srcs, where).not.toContain(wrong);
    }
  });

  it("never renders a wordmark whose ink matches its background", () => {
    /* The whole failure mode in one assertion: dark ink on the dark UI, or
       light ink on a light one, is an invisible logo that loads successfully
       and so trips no error handler. */
    for (const scheme of ["dark", "light"]) {
      const wrong = scheme === "dark" ? "/logo-name-light.svg" : "/logo-name-dark.svg";
      for (const isMobile of [false, true]) {
        expect(srcsForScheme(scheme, { isMobile })).not.toContain(wrong);
      }
    }
  });
});
