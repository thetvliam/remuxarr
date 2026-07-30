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
 ═ ═*══════════════════════════════════**═══════════════════════════════════════ */

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
  const base = color.length > 7 ? color.slice(0, 7) : color;
  const hex  = Math.round(Math.max(0, Math.min(1, amount)) * 255)
  .toString(16)
  .padStart(2, "0");
  return `${base}${hex}`;
};

/* Named opacities, matching the hex suffixes previously hardcoded.
 * Shared across themes — a theme changes colours, not what "subtle" means. */
export const ALPHA = {
  faint:  0.016,  // was "04"
  ghost:  0.027,  // was "07"
  hint:   0.031,  // was "08"
  subtle: 0.047,  // was "0c"
  trace:  0.071,  // was "12"
  soft:   0.078,  // was "14"
  mild:   0.125,  // was "20"
  low:    0.094,  // was "18"
  medium: 0.133,  // was "22"
  firm:   0.2,    // was "33"
  half:   0.533,  // was "88"
  strong: 0.267,  // was "44"
  heavy:  0.333,  // was "55"
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
 ═ ═*══════════════════════════════════**═══════════════════════════════════════ */
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
  palette: terminalPalette,
  statusColor: buildStatusColor(terminalPalette),
  levelColor: buildLevelColor(terminalPalette),
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
    root:   "'JetBrains Mono', 'Courier New', monospace",
    /* Log output is deliberately monospaced — column alignment carries
     * meaning there, so it does not follow `family`. */
    mono:   "'Courier New', 'Lucida Console', monospace",
    size:   { xs: 9, sm: 10, md: 11, base: 12, lg: 13, xl: 14, xxl: 15, h2: 16, h1: 18 },
    weight: { normal: 400, medium: 500, semibold: 600, bold: 700, black: 900 },
    tracking: {
      tight: "0.03em", snug: "0.06em", normal: "0.08em", wide: "0.1em",
      wider: "0.12em", widest: "0.14em", ultra: "0.16em", max: "0.18em",
    },
    /* Line height belongs to the type scale, not to spacing: it scales with
     * the font size rather than with padding, and a roomier theme wants
     * looser leading at every step. */
    leading: { none: 1, snug: 1.55, normal: 1.6, relaxed: 1.65, loose: 1.7 },
  },
  radius: { none: 0, sm: 0, pill: 11, full: "50%" },
  space:  { none: 0, hair: 2, xxs: 4, xs: 6, sm: 8, md: 10, lg: 12, xl: 16, xxl: 20, huge: 24, max: 28 },
  /* Off-scale one-offs, preserved exactly so the default theme renders
   * unchanged. A new theme is free to give these scale-aligned values —
   * that is precisely how a theme alters density without moving anything. */
  legacy: {
    badgePadY: 1,  badgePadX: 6,
    btnPadY:   5,  btnPadX:   13,
    emptyPadY: 38, emptyPadX: 16,
    statGapY:  3,
    ledSize:   7,  ledGlow:   5,  ledGlowFar: 10,
    badgeFallbackBg: "#111",
    badgeRadius: 0,
    barHeight: 3,
    /* bars/ */
    miniBarGap:    1,
    segBarGap:     2,
    segBarHeight:  13,
    /* layout/ */
    panelHeadPadY:  7,
    panelHeadPadX:  14,
    panelCountPadX: 5,
    toastOffset:      20,
    toastPadY:        8,
    toastPadX:        14,
    toastAccent:      3,
    toastMinW:        210,
    toastMaxW:        360,
    toastLine:        1.5,
    toastMobileInset: 32,
    /* dashboard/ */
    activePadY:  14,
    accentWidth: 3,
    dryRunBg:    "#1a1400",
    abortPadY:   3,
    abortPadX:   11,
    ledSizeLg:   8,
    rowPadX:       14,
    queueRowPadY:  9,
    rowLabelGapY:  3,
    ledSizeSm:     6,
    clearPadY:     2,
    clearPadX:     9,
    rowHoverBg:    "#ffffff07",
    tabPadY:       2,
    tabPadX:       10,
    headerHeight:  46,
    headerLogoGap: 22,
    apiBarW:       210,
    navPadY:       3,
    navPadX:       10,
    drawerPadY:    13,
    drawerPadX:    18,
    scrimBg:       "#00000066",
    logoInk:       "#000",
    /* Thin active-state accent stroke: mobile tab underline, settings nav
     * item left border, modal top border. Distinct from accentWidth (3). */
    accentThin:    2,
    /* review/ */
    pagePadY:      28,
    pagePadX:      22,
    cardPadY:      14,
    sectionGapY:   32,
    labelGapY:     5,
    subGapY:       7,
    descGapY:      14,
    inputPadY:     5,
    reviewBorder:  "#3a2800",
    trackRowBg:    "#00000022",
    rowSelectedBg: "#ffffff08",
    /* settings/ */
    sectionSepGapY:   36,
    fieldRowPadY:     13,
    settingRowPadY:   14,
    ctlPadY:          3,
    actionPadX:       14,
    savePadX:         18,
    navPadR:          18,
    navItemPadY:      9,
    navUnsavedGapY:   14,
    chipPadY:         1,
    chipPadX:         7,
    tagPadY:          2,
    tagPadX:          7,
    tagGap:           5,
    nudge:            1,
    colGap:           26,
    mobileFieldGap:   9,
    pageBotPad:       48,
    pageBotPadMobile: 40,
    blankPad:         32,
    logBg:            "#0d0f1a",
    logMeta:          "#3a4060",
    logText:          "#c8cce8",
    zebraBg:          "#ffffff04",
    /* DetailModal */
    modalPadX:      18,
    modalFootPadY:  7,
    modalScrimBg:   "#000000bb",
    closeGlyph:       20,
    closeGlyphMobile: 24,
    errorBg:        "#180a0a",
    /* App */
    guardScrimBg: "rgba(0,0,0,0.66)",
    guardPad:     22,
    guardPadB:    18,
    guardBtnPadY: 7,
  },
};

/* ═══════════════════════════════════════════════════════════════════════════
 * THEME: soft (demonstration)
 * Same skeleton, different clothes — rounded corners, slightly larger type,
 * roomier padding, calmer palette. Included to prove the mechanism handles
 * STRUCTURAL change, not just colour. Replace with your real mockups.
 ═ ═*══════════════════════════════════**═══════════════════════════════════════ */
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
  palette: softPalette,
  statusColor: buildStatusColor(softPalette),
  levelColor: buildLevelColor(softPalette),
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
    root:   "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    mono:   "ui-monospace, 'SF Mono', Menlo, monospace",
    /* One step larger throughout — same hierarchy, softer density. */
    size:   { xs: 10, sm: 11, md: 12, base: 13, lg: 14, xl: 15, xxl: 16, h2: 17, h1: 20 },
    weight: { normal: 400, medium: 500, semibold: 600, bold: 600, black: 800 },
    /* Much tighter tracking — the single biggest driver of the
     * "terminal vs. modern app" feel. */
    tracking: {
      tight: "0", snug: "0.01em", normal: "0.02em", wide: "0.03em",
      wider: "0.04em", widest: "0.05em", ultra: "0.06em", max: "0.08em",
    },
    /* Looser at every step than terminal's. */
    leading: { none: 1, snug: 1.6, normal: 1.65, relaxed: 1.7, loose: 1.75 },
  },
  radius: { none: 0, sm: 6, pill: 999, full: "50%" },
  space:  { none: 0, hair: 3, xxs: 5, xs: 8, sm: 10, md: 13, lg: 16, xl: 20, xxl: 26, huge: 30, max: 34 },
  legacy: {
    badgePadY: 3,  badgePadX: 9,
    btnPadY:   7,  btnPadX:   16,
    emptyPadY: 44, emptyPadX: 20,
    statGapY:  5,
    ledSize:   8,  ledGlow:   6,  ledGlowFar: 12,
    badgeFallbackBg: "#1d2029",
    badgeRadius: 4,
    barHeight: 4,
    /* bars/ */
    miniBarGap:    2,
    segBarGap:     3,
    segBarHeight:  14,
    /* layout/ */
    panelHeadPadY:  10,
    panelHeadPadX:  16,
    panelCountPadX: 7,
    toastOffset:      24,
    toastPadY:        11,
    toastPadX:        16,
    toastAccent:      3,
    toastMinW:        230,
    toastMaxW:        380,
    toastLine:        1.55,
    toastMobileInset: 32,
    /* dashboard/ */
    activePadY:  18,
    accentWidth: 3,
    dryRunBg:    "#241d06",
    abortPadY:   5,
    abortPadX:   14,
    ledSizeLg:   9,
    rowPadX:       16,
    queueRowPadY:  12,
    rowLabelGapY:  4,
    ledSizeSm:     7,
    clearPadY:     4,
    clearPadX:     11,
    rowHoverBg:    "#ffffff0a",
    tabPadY:       4,
    tabPadX:       13,
    headerHeight:  52,
    headerLogoGap: 24,
    apiBarW:       230,
    navPadY:       5,
    navPadX:       13,
    drawerPadY:    15,
    drawerPadX:    20,
    scrimBg:       "#00000073",
    logoInk:       "#000",
    /* Thin active-state accent stroke: mobile tab underline, settings nav
     * item left border, modal top border. Distinct from accentWidth (3). */
    accentThin:    2,
    /* review/ */
    pagePadY:      32,
    pagePadX:      26,
    cardPadY:      18,
    sectionGapY:   38,
    labelGapY:     7,
    subGapY:       9,
    descGapY:      16,
    inputPadY:     7,
    reviewBorder:  "#3a2c0c",
    trackRowBg:    "#00000033",
    rowSelectedBg: "#ffffff0d",
    /* settings/ */
    sectionSepGapY:   42,
    fieldRowPadY:     16,
    settingRowPadY:   18,
    ctlPadY:          5,
    actionPadX:       16,
    savePadX:         22,
    navPadR:          22,
    navItemPadY:      11,
    navUnsavedGapY:   16,
    chipPadY:         2,
    chipPadX:         9,
    tagPadY:          3,
    tagPadX:          9,
    tagGap:           7,
    nudge:            2,
    colGap:           30,
    mobileFieldGap:   11,
    pageBotPad:       56,
    pageBotPadMobile: 48,
    blankPad:         38,
    logBg:            "#1b1f2b",
    logMeta:          "#575f7d",
    logText:          "#dde0ec",
    zebraBg:          "#ffffff07",
    /* DetailModal */
    modalPadX:      22,
    modalFootPadY:  9,
    modalScrimBg:   "#000000cc",
    closeGlyph:       22,
    closeGlyphMobile: 26,
    errorBg:        "#241010",
    /* App */
    guardScrimBg: "rgba(0,0,0,0.72)",
    guardPad:     26,
    guardPadB:    22,
    guardBtnPadY: 9,
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

  /* The page background sits on <body>, outside React's tree, so it has to
   * be pushed there explicitly or a light theme would leave a dark gutter
   * around the app. */
  useEffect(() => {
    const t = themes[themeId] || terminal;
    document.body.style.background = t.palette.bg;
    document.body.style.color      = t.palette.text;
  }, [themeId]);

  const value = useMemo(() => ({
    ...(themes[themeId] || terminal),
                               themeId,
                               setThemeId,
  }), [themeId]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
};

/* Default-theme values for modules that cannot use a hook (plain .js
 * helpers, and components not yet migrated). These do NOT follow theme
 * changes — anything importing them still renders in the default theme,
 * which is why migration needs to finish rather than stopping half-done. */
export const palette = terminal.palette;
export const type    = terminal.type;
export const radius  = terminal.radius;
export const space   = terminal.space;
export const legacy  = terminal.legacy;
