/* ═══════════════════════════════════════════════════════════════════════════
 * THEME — single source of truth for every visual value
 *
 * A theme is NOT just a colour scheme. It carries the type scale, spacing,
 * padding and corner radii as well, so switching themes can take the same
 * screen from sharp/dense to soft/roomy without a single element changing
 * position. The skeleton — what is laid out where, and in what order — is
 * fixed by the components; a theme only changes how that skeleton is
 * dressed.
 *
 * That is why the whole theme is delivered through React context rather
 * than as a plain import: structural values change at runtime too, so
 * components must re-read them when the theme changes. Components pull
 * what they need with the useTheme() hook:
 *
 *     const { palette, type, space, radius } = useTheme();
 *
 * CSS custom properties were considered and rejected: they would force
 * every numeric token into an opaque string, which breaks any component
 * that does arithmetic with a size (LED glow radii, computed offsets) and
 * makes conditional logic on token values impossible.
 *
 * ── Adding a theme ────────────────────────────────────────────────────────
 * Copy a block below, change the values, add it to `themes`. Keep every key
 * present — a missing key is a runtime undefined, not a fallback. Keep the
 * SHAPE identical; only values should differ.
 ═ ═*══════════════════════════════════*════════════════════════════*═══════════ */

import { createContext, useContext, useEffect, useMemo, useState } from "react";

/* ── alpha() ──────────────────────────────────────────────────────────────
 * Returns a colour at a given opacity (0–1).
 *
 * Replaces the `palette.amber + "18"` pattern from ~48 sites, which
 * appended a raw hex alpha suffix and therefore only worked while every
 * colour was 6-digit hex. Verified to reproduce each suffix previously in
 * use byte-for-byte, so migrating a call site changes nothing on screen.
 * Non-hex input (rgb/hsl/named) falls back to color-mix().
 */
export const alpha = (color, amount) => {
  if (typeof color !== "string" || !color.startsWith("#")) {
    return `color-mix(in srgb, ${color} ${Math.round(amount * 100)}%, transparent)`;
  }
  // Expand 3- and 4-digit hex before appending. Without this, "#111" came
  // straight through and became "#11180" — a five-digit string CSS silently
  // ignores, so the element renders with no background at all rather than a
  // faint one. Two surface tokens are written short (#111, #000), so this
  // was one call away from being a real bug.
  let base = color.length > 7 ? color.slice(0, 7) : color;
  if (base.length === 4 || base.length === 5) {
    base = "#" + base.slice(1, 4).split("").map(c => c + c).join("");
  }
  const hex  = Math.round(Math.max(0, Math.min(1, amount)) * 255)
  .toString(16)
  .padStart(2, "0");
  return `${base}${hex}`;
};

/* Named opacities, matching the hex suffixes previously hardcoded.
 * Shared across themes — a theme changes colours, not what "subtle" means.
 *
 * Listed strictly ascending, which is the only way the names carry any
 * information. They were not: mild (0.125) sat above low (0.094) and half
 * (0.533) above strong (0.267) and heavy (0.333), so reading down the list
 * gave no sense of which name means "more" and picking a neighbour of an
 * existing value could move the opacity the wrong way.
 *
 * The `was` comments are the migration audit trail: each value is chosen so
 * alpha() reproduces that exact hex suffix, and they are how that stays
 * checkable.
 *
 * The four faintest rungs have no call sites today. They are kept because
 * this is a scale rather than a set of tokens — a theme wanting a barely
 * visible overlay should find a rung already named rather than invent one
 * and a naming convention with it. */
export const ALPHA = {
  faint:  0.016,  // was "04"  — unused
  ghost:  0.027,  // was "07"  — unused
  hint:   0.031,  // was "08"  — unused
  subtle: 0.047,  // was "0c"  — unused
  trace:  0.071,  // was "12"
  soft:   0.078,  // was "14"
  low:    0.094,  // was "18"
  mild:   0.125,  // was "20"
  medium: 0.133,  // was "22"
  firm:   0.2,    // was "33"
  strong: 0.267,  // was "44"
  heavy:  0.333,  // was "55"
  half:   0.533,  // was "88"
};

/* Stacking order, in one place. Shared across themes for the same reason
 * ALPHA is: a theme changes how things look, not what sits in front of what.
 *
 * These were nine bare numbers spread across five files — 5, 6, 490, 500,
 * 550, 600, 1000, 1100, 2000 — with no way to see the order without
 * grepping all five. The gaps between them are not arbitrary but nothing
 * recorded why, so the safe move when adding anything was to pick a bigger
 * number, which is how a codebase ends up with a 99999.
 *
 * Values are unchanged from what they replace, so the rendered order is
 * identical. Read top to bottom, this IS the stacking order.
 *
 * The two sticky rungs never coexist — one is the desktop settings save
 * bar, the other the mobile settings nav — but they are named separately
 * because they answer to different layouts. */
export const LAYER = {
  stickySaveBar:  5,     // settings save bar, sticks under the page header
  stickyNav:      6,     // mobile settings nav
  drawerScrim:    490,   // mobile drawer backdrop
  drawer:         500,   // mobile drawer panel, above its own scrim
  headerRow:      550,   // mobile header row, so the drawer slides beneath it
  header:         600,   // the header bar
  modal:          1000,  // DetailModal
  guardModal:     1100,  // unsaved-changes prompt, above any modal it guards
  toast:          2000,  // always on top: a toast may report a failure in
  // whatever is underneath, so it can never be hidden
};

/* Build the status/action colour maps from a palette. These are colour
 * lookups, so they must be rebuilt per theme rather than frozen at import. */
const buildStatusColor = (p) => ({
  pending:       p.dim,
  processing:    p.blue,
  success:       p.green,
  failed:        p.red,
  manual_review: p.yellow,
  skipped:       p.dim,
  cancelled:     p.dim,
  dry_run:       p.violet,

  // Forge job statuses. Absent before, so statusColor[job.status] was
  // undefined for any forge row and ForgeProcessedPanel carried its own
  // ternary instead — a second, partial copy of this map that no other
  // consumer knew about. Any new view rendering a forge job would have hit
  // the same gap and silently produced an uncoloured indicator.
  undo_pending:  p.blue,     // in flight, mirrors "processing"
  undone:        p.dim,      // terminal; the file is a candidate again
  undo_failed:   p.red,      // the AC3 track is still present
});

/* Toast tones. Callers name the MEANING of a message — "error", "success" —
 * and the theme decides the colour. Previously every toast() call passed a
 * palette value directly, which meant the data hooks had to read the theme
 * to raise a message, coupling the whole data layer to appearance for the
 * sake of one argument. It also went wrong quietly: a colour captured in a
 * callback stayed captured, so a toast could arrive wearing the previous
 * theme's palette long after the switch.
 *
 * Naming the tone instead means the colour is resolved at render, by the
 * component doing the rendering, from the theme that is current then. There
 * is no colour to capture and nothing to go stale.
 *
 * The eight tones are the ones actually in use, not an invented taxonomy —
 * collapsing them further would silently merge distinctions the app already
 * makes, like the violet reserved for dry-run previews. */
const buildToastTone = (p) => ({
  success: p.green,   // completed, resumed
  error:   p.red,     // failed, rejected, unreachable
  warning: p.yellow,  // a mode is on that changes behaviour: dry run, paused
  notice:  p.amber,   // an action was taken: scan started, re-queued, moved
  info:    p.blue,    // neutral progress: queued, cleaned up, undone
  preview: p.violet,  // dry-run output ready — deliberately its own colour
  neutral: p.muted,   // quiet acknowledgement: dismissed, removed
  quiet:   p.dim,     // quietest: a background preference was toggled
});

/* Log severity colours. Was a module-level const in LogViewer.jsx built from
 * the static palette, which froze the log output to the default theme even
 * once the surrounding page followed the switch. */
const buildLevelColor = (p) => ({
  DEBUG:    p.dim,
  INFO:     p.muted,
  WARNING:  p.amber,
  ERROR:    p.red,
  CRITICAL: p.red,
});

const buildActionCfg = (p, tint) => ({
  copy_track:         { bg: tint.green,  border: tint.greenB,  text: p.green,   label: "COPY"      },
  drop_track:         { bg: tint.red,    border: tint.redB,    text: p.red,     label: "DROP"      },
  transcode_track:    { bg: tint.amber,  border: tint.amberB,  text: p.amber,   label: "TRANSCODE" },
  change_container:   { bg: tint.blue,   border: tint.blueB,   text: p.blue,    label: "CONVERT"   },
  flag_manual_review: { bg: tint.yellow, border: tint.yellowB, text: p.yellow,  label: "FLAG"      },
  extract_subtitle:   { bg: tint.cyan,   border: tint.cyanB,   text: p.cyan,    label: "EXTRACT"   },
  add_faststart:      { bg: tint.violet, border: tint.violetB, text: p.violet,  label: "FASTSTART" },
});

/* ═══════════════════════════════════════════════════════════════════════════
 * THEME: terminal (default)
 * The current look, value-for-value. Sharp corners, dense spacing, wide
 * letter-spacing, small type.
 ═ ═*══════════════════════════════════*════════════════════════════*═══════════ */
const terminalPalette = {
  bg:     "#07080b",
  card:   "#0d0f14",
  border: "#181b24",
  text:   "#c4c8d8",
  dim:    "#3a3f58",
  muted:  "#5a607a",
  amber:  "#e89a0a",
  green:  "#1cb85e",
  red:    "#d93535",
  blue:   "#4080f0",
  yellow: "#d4920a",
  violet: "#9d6df0",
  cyan:   "#2dd4d4",
};

const terminal = {
  id:    "terminal",
  label: "Terminal",
  /* Drives the CSS color-scheme property, which is what makes the browser
   * render checkboxes, file pickers, select popups and scrollbar corners to
   * match. Without it every native control is drawn light-on-white against a
   * dark page. A light theme sets "light" here and gets the inverse for
   * free — this is not something the palette can express. */
  colorScheme: "dark",
  blurb: "Dense and sharp. Wide letter-spacing, square corners, tight rows.",
  palette: terminalPalette,
  statusColor: buildStatusColor(terminalPalette),
  levelColor: buildLevelColor(terminalPalette),
  toastTone:  buildToastTone(terminalPalette),
  actionCfg: buildActionCfg(terminalPalette, {
    green: "#091a0f", greenB: "#122a1a",
    red:   "#1a0909", redB:   "#2a1212",
    amber: "#1a1200", amberB: "#2a1e00",
    blue:  "#090f1a", blueB:  "#12182a",
    yellow:"#1a1000", yellowB:"#2a1c00",
    cyan:  "#001a1a", cyanB:  "#0f2a2a",
    violet:"#0d001a", violetB:"#1e0a2a",
  }),
  type: {
    family: "inherit",
    /* The app shell's font stack. Everything else inherits it via `family`,
     * so this one value carries most of the theme's character. */
    root:   "'JetBrains Mono Variable', 'JetBrains Mono', 'Courier New', monospace",
    /* Log output is deliberately monospaced — column alignment carries
     * meaning there, so it does not follow `family`. */
    mono:   "'Courier New', 'Lucida Console', monospace",
    size:   { xs: 9, sm: 10, md: 11, base: 12, lg: 13, xl: 14, xxl: 15, h2: 16, h1: 18 },
    /* black is 800, not 900: JetBrains Mono ships no 900 face, so a 900
     * here could only ever be synthesised. Every weight in this map has
     * a real file imported in fonts.js. */
    weight: { normal: 400, medium: 500, semibold: 600, bold: 700, black: 800 },
    tracking: {
      tight: "0.03em", snug: "0.06em", normal: "0.08em", wide: "0.1em",
      wider: "0.12em", widest: "0.14em", ultra: "0.16em", max: "0.18em",
    },
    /* Line height belongs to the type scale, not to spacing: it scales with
     * the font size rather than with padding, and a roomier theme wants
     * looser leading at every step. */
    leading: { none: 1, tight: 1.5, snug: 1.55, normal: 1.6, relaxed: 1.65, loose: 1.7 },
  },
  /* pill is 999, not a real radius: a value larger than half the box makes
   * CSS clamp to exactly half, so any height gives a true pill. It was 11
   * here — half the toggle switch's 22px height, hardcoded in
   * MaintenanceSection. Changing that height would have squared the corners
   * with nothing in either file to connect the two. */
  /* badge is its own rung so each theme decides whether the small status
   * pills follow the rest of its rounding. Three atoms hardcoded
   * radius.none, which is 0 in every theme by definition — invisible here
   * where sm is also 0, but on a rounded theme every other pill curved and
   * those three stayed square with nothing saying why. */
  radius: { none: 0, sm: 0, badge: 0, pill: 999, full: "50%" },
  space:  { none: 0, hair: 2, xxs: 4, xs: 6, sm: 8, md: 10, lg: 12, xl: 16,
    xxl: 20, huge: 24, max: 28, xxxl: 32, giant: 40, mega: 48 },
    /* Component geometry — the fixed dimensions of the app's own furniture,
     * as distinct from the spacing rhythm on the `space` scale above. These
     * are sizes of things, not gaps between things, which is why they are not
     * on a scale: an LED is 7px because that reads as a status dot, not
     * because 7 is a step in a series.
     *
     * Mostly leave-alone when defining a new theme. A denser or roomier theme
     * scales these a little; nothing here needs rethinking the way the
     * surfaces below do.
     *
     * Two are load-bearing and must not be nudged casually: headerHeight
     * drives both the bar height and the mobile drawer's top offset, and the
     * ledGlow radii are tuned to the ledSize values. */
    size: {
      ledSize:          7,
      ledGlow:          5,
      ledGlowFar:       10,
      barHeight:        3,
      segBarHeight:     13,
      toastOffset:      20,
      toastAccent:      3,
      toastMinW:        210,
      toastMaxW:        360,
      toastMobileInset: 32,
      accentWidth:      3,
      ledSizeLg:        8,
      ledSizeSm:        6,
      headerHeight:     46,
      apiBarW:          210,
      scrollbarW:       3,
      focusRing:        2,
      focusOffset:      1,
      accentThin:       2,
      closeGlyph:       20,
      closeGlyphMobile: 24,
    },

    /* Per-theme surfaces — colours with no home in the palette. Overlays,
     * scrims, shadows and one-off backgrounds, all of which sit ON something
     * rather than being a colour in their own right.
     *
     * These are the entries a new theme genuinely has to think about, and the
     * reason they cannot be derived with alpha(): every one of them darkens
     * what is beneath it, because both current themes are dark. A light theme
     * has to lighten instead, which is a different colour and not a different
     * opacity of the same one. */
    surface: {
      badgeFallbackBg:  "#111",
      dryRunBg:         "#1a1400",
      rowHoverBg:       "#ffffff07",
      drawerShadow:     "0 4px 16px #00000066",
      logoInk:          "#000",
      reviewBorder:     "#3a2800",
      trackRowBg:       "#00000022",
      rowSelectedBg:    "#ffffff08",
      logBg:            "#0d0f1a",
      logMeta:          "#3a4060",
      logText:          "#c8cce8",
      zebraBg:          "#ffffff04",
      modalScrimBg:     "#000000bb",
      errorBg:          "#180a0a",
      guardScrimBg:     "rgba(0,0,0,0.66)",
    },
};

/* ═══════════════════════════════════════════════════════════════════════════
 * THEME: soft (demonstration)
 * Same skeleton, different clothes — rounded corners, slightly larger type,
 * roomier padding, calmer palette. Included to prove the mechanism handles
 * STRUCTURAL change, not just colour. Replace with your real mockups.
 ═ ═*══════════════════════════════════*════════════════════════════*═══════════ */
const softPalette = {
  bg:     "#12141a",
  card:   "#191c25",
  border: "#262a36",
  text:   "#d7dae6",
  dim:    "#4d5470",
  muted:  "#6d7590",
  amber:  "#f0a93a",
  green:  "#3ecb7d",
  red:    "#e85555",
  blue:   "#5f97f5",
  yellow: "#e0a63a",
  violet: "#af86f5",
  cyan:   "#4fdede",
};

const soft = {
  id:    "soft",
  label: "Soft",
  colorScheme: "dark",
  blurb: "Roomier and rounder. Larger type, tight tracking, generous padding.",
  palette: softPalette,
  statusColor: buildStatusColor(softPalette),
  levelColor: buildLevelColor(softPalette),
  toastTone:  buildToastTone(softPalette),
  actionCfg: buildActionCfg(softPalette, {
    green: "#0e2417", greenB: "#1a3a27",
    red:   "#241010", redB:   "#3a1c1c",
    amber: "#241a06", amberB: "#3a2c0c",
    blue:  "#101724", blueB:  "#1c273a",
    yellow:"#241a06", yellowB:"#3a2a0c",
    cyan:  "#062424", cyanB:  "#0c3a3a",
    violet:"#160a24", violetB:"#26143a",
  }),
  type: {
    family: "inherit",
    root:   "'Inter Variable', 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    mono:   "ui-monospace, 'SF Mono', Menlo, monospace",
    /* One step larger throughout — same hierarchy, softer density. */
    size:   { xs: 10, sm: 11, md: 12, base: 13, lg: 14, xl: 15, xxl: 16, h2: 17, h1: 20 },
    /* bold is 700, giving the same even 100 step as terminal. It was 600,
     * identical to semibold, which collapsed the emphasis distinction on
     * the app's most-used weight — 49 call sites asking for bold rendered
     * no heavier than the 21 asking for semibold. Inter's axis spans
     * 100-900, so 700 is a real face. */
    weight: { normal: 400, medium: 500, semibold: 600, bold: 700, black: 800 },
    /* Much tighter tracking — the single biggest driver of the
     * "terminal vs. modern app" feel. */
    tracking: {
      tight: "0", snug: "0.01em", normal: "0.02em", wide: "0.03em",
      wider: "0.04em", widest: "0.05em", ultra: "0.06em", max: "0.08em",
    },
    /* Looser at every step than terminal's. */
    leading: { none: 1, tight: 1.55, snug: 1.6, normal: 1.65, relaxed: 1.7, loose: 1.75 },
  },
  radius: { none: 0, sm: 6, badge: 6, pill: 999, full: "50%" },
  space:  { none: 0, hair: 3, xxs: 5, xs: 8, sm: 10, md: 13, lg: 16, xl: 20,
    xxl: 26, huge: 30, max: 34, xxxl: 38, giant: 48, mega: 58 },
    /* Component geometry — the fixed dimensions of the app's own furniture,
     * as distinct from the spacing rhythm on the `space` scale above. These
     * are sizes of things, not gaps between things, which is why they are not
     * on a scale: an LED is 7px because that reads as a status dot, not
     * because 7 is a step in a series.
     *
     * Mostly leave-alone when defining a new theme. A denser or roomier theme
     * scales these a little; nothing here needs rethinking the way the
     * surfaces below do.
     *
     * Two are load-bearing and must not be nudged casually: headerHeight
     * drives both the bar height and the mobile drawer's top offset, and the
     * ledGlow radii are tuned to the ledSize values. */
    size: {
      ledSize:          8,
      ledGlow:          6,
      ledGlowFar:       12,
      barHeight:        4,
      segBarHeight:     14,
      toastOffset:      24,
      toastAccent:      3,
      toastMinW:        230,
      toastMaxW:        380,
      toastMobileInset: 32,
      accentWidth:      3,
      ledSizeLg:        9,
      ledSizeSm:        7,
      headerHeight:     52,
      apiBarW:          230,
      scrollbarW:       5,
      focusRing:        2,
      focusOffset:      2,
      accentThin:       2,
      closeGlyph:       22,
      closeGlyphMobile: 26,
    },

    /* Per-theme surfaces — colours with no home in the palette. Overlays,
     * scrims, shadows and one-off backgrounds, all of which sit ON something
     * rather than being a colour in their own right.
     *
     * These are the entries a new theme genuinely has to think about, and the
     * reason they cannot be derived with alpha(): every one of them darkens
     * what is beneath it, because both current themes are dark. A light theme
     * has to lighten instead, which is a different colour and not a different
     * opacity of the same one. */
    surface: {
      badgeFallbackBg:  "#1d2029",
      dryRunBg:         "#241d06",
      rowHoverBg:       "#ffffff0a",
      drawerShadow:     "0 6px 24px #0000004d",
      logoInk:          "#000",
      reviewBorder:     "#3a2c0c",
      trackRowBg:       "#00000033",
      rowSelectedBg:    "#ffffff0d",
      logBg:            "#1b1f2b",
      logMeta:          "#575f7d",
      logText:          "#dde0ec",
      zebraBg:          "#ffffff07",
      modalScrimBg:     "#000000cc",
      errorBg:          "#241010",
      guardScrimBg:     "rgba(0,0,0,0.72)",
    },
};

export const themes = { terminal, soft };
export const DEFAULT_THEME_ID = "terminal";
const STORAGE_KEY = "remuxarr.theme";

/* ── Context ──────────────────────────────────────────────────────────── */
const ThemeContext = createContext({
  ...terminal,
  themeId: DEFAULT_THEME_ID,
  setThemeId: () => {},
});

/** Access the active theme. Destructure only what the component needs. */
export const useTheme = () => useContext(ThemeContext);

export const ThemeProvider = ({ children }) => {
  const [themeId, setThemeId] = useState(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      return saved && themes[saved] ? saved : DEFAULT_THEME_ID;
    } catch (_) { return DEFAULT_THEME_ID; }
  });

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, themeId); } catch (_) { /* ignore */ }
  }, [themeId]);

  /* The page background sits outside React's tree, so it has to be pushed
   * there explicitly or a light theme would leave a dark gutter around the
   * app. Both <html> and <body> are set: <body> alone leaves <html> showing
   * the static background in index.html, which is visible in the overscroll
   * area on any theme that is not the one that value was written for. */
  useEffect(() => {
    const t = themes[themeId] || terminal;
    document.documentElement.style.background = t.palette.bg;
    document.body.style.background = t.palette.bg;
    document.body.style.color      = t.palette.text;
  }, [themeId]);

  /* Global CSS that follows the theme. Everything here needs a stylesheet
   * rather than an inline style — pseudo-elements, pseudo-classes and
   * scrollbars have no element to set properties on — so it cannot live
   * alongside the background above. Grouped with it because all of it is
   * global and all of it changes with the theme. */
  useEffect(() => {
    const t = themes[themeId] || terminal;
    const style = document.createElement("style");
    style.textContent = `
    /* Tells the browser which way to draw the controls it owns: checkboxes,
     * file pickers, select popups, scrollbar corners. Unset, they render
     * light-on-white against a dark page. */
    :root { color-scheme: ${t.colorScheme}; accent-color: ${t.palette.amber}; }

    ::-webkit-scrollbar       { width: ${t.size.scrollbarW}px; }
    ::-webkit-scrollbar-thumb { background: ${t.palette.border}; }

    /* Placeholders were browser-default grey everywhere. */
    ::placeholder { color: ${t.palette.dim}; opacity: 1; }

    /* Focus. Every text input, search box and select in the app carried an
     * inline outline: none with nothing put back, so keyboard and
     * screen-reader users had no focus indicator anywhere. Those inline
     * declarations are gone; the suppression now lives here, where it can be
     * limited to :focus and paired with a :focus-visible ring that the
     * browser shows for keyboard navigation but not for mouse clicks.
     *
     * It has to be a stylesheet rule for a second reason beyond the pseudo-
     * class: an inline style beats any rule without !important, so as long
     * as outline: none stayed on the elements, no ring could have applied. */
    :focus { outline: none; }
    :focus-visible {
      outline: ${t.size.focusRing}px solid ${t.palette.amber};
      outline-offset: ${t.size.focusOffset}px;
    }
    `;
    document.head.appendChild(style);
    return () => { document.head.removeChild(style); };
  }, [themeId]);

  /* Mobile browser chrome. The meta tag ships with a static value, so the
   * address bar stayed the default theme's colour whatever was selected. */
  useEffect(() => {
    const t = themes[themeId] || terminal;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", t.palette.bg);
  }, [themeId]);

    const value = useMemo(() => ({
      ...(themes[themeId] || terminal),
                                 themeId,
                                 setThemeId,
    }), [themeId]);

    return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
};

/* There are deliberately no static value exports here. They existed so that
 * unmigrated components could keep working, and they were frozen: anything
 * importing them rendered in the default theme no matter what the user
 * picked. Migration is finished, nothing imports them, and re-adding one
 * would silently opt a component out of theming. Use useTheme(). */
