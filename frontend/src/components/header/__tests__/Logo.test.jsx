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
 * prefers-color-scheme, deliberately. Remuxarr picks its own palette, and both
 * themes it currently ships are dark, so following the system would give a
 * user whose OS is set to light black lettering on Remuxarr's dark header.
 * The light case below is therefore forward-looking: it fixes the contract now
 * so that adding a light theme is a one-line change to theme.jsx and nothing
 * else.
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

describe("header logo", () => {
  it("uses the light-ink wordmark on a dark theme", () => {
    const srcs = srcsForScheme("dark");

    expect(srcs).toContain("/logo-name-dark.svg");
    expect(srcs).not.toContain("/logo-name-light.svg");
  });

  it("uses the dark-ink wordmark if a light theme is ever added", () => {
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
