/* ═══════════════════════════════════════════════════════════════════════════
 * THEME — single source of truth for every visual value
 *
 * Phase 0 of the theming refactor. Every value here is IDENTICAL to what was
 * previously hardcoded inline, so adopting it changes nothing on screen. That
 * is deliberate: it means any visual difference you spot while migrating a
 * component is a mistake, not a design decision, which makes a large
 * mechanical refactor safe to do in small batches.
 *
 * Two distinct kinds of value live here, and the distinction matters:
 *
 *   palette  — COLOURS. These are what a theme actually swaps. Everything
 *              here is expected to differ between dark/light/custom themes.
 *
 *   type / radius / space — STRUCTURE. A theme does not change these; a
 *              light theme has the same 9px badge text and the same 6px
 *              gaps as the dark one. They live here purely to be defined
 *              once instead of 400 times.
 *
 * Because only the palette varies at runtime, structural tokens can stay a
 * plain import (no React context, no re-render plumbing) while the palette
 * later moves to CSS custom properties for instant switching.
 ═ ═*═════════════════════════════════════════════════════════════════════════ */

/* ── Palette ──────────────────────────────────────────────────────────────
 * Values lifted verbatim from constants.js. constants.js now re-exports
 * these as `C`, so all existing imports keep working untouched while
 * components migrate one directory at a time.
 */
export const palette = {
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
};

/* ── alpha() ──────────────────────────────────────────────────────────────
 * Returns a colour at the given opacity.
 *
 * This replaces the `C.amber + "18"` pattern used in ~48 places, which
 * appends a raw 8-digit-hex alpha suffix directly onto a colour string.
 * That trick works only while every palette value is 6-digit hex — it
 * breaks silently the moment a value is rgb(), hsl(), a named colour, or
 * (critically) a CSS custom property, which is exactly what Phase 3 needs
 * for runtime theme switching. Routing opacity through a function now
 * means Phase 3 changes one implementation instead of hunting 48 call
 * sites.
 *
 * `amount` is 0–1. Hex output is preserved for now so migrated components
 * render byte-identically to the string-concat version they replace:
 *   alpha(palette.amber, 0.09) === "#e89a0a17"   (0.09 * 255 = 22.95 → 17)
 *
 * The helper accepts a pre-existing 8-digit value and replaces its alpha,
 * so double-application is safe.
 */
export const alpha = (color, amount) => {
  if (typeof color !== "string" || !color.startsWith("#")) {
    // Non-hex (rgb(), var(), named) — fall back to color-mix so this keeps
    // working once the palette becomes CSS custom properties in Phase 3.
    return `color-mix(in srgb, ${color} ${Math.round(amount * 100)}%, transparent)`;
  }
  const base = color.length > 7 ? color.slice(0, 7) : color;
  const hex  = Math.round(Math.max(0, Math.min(1, amount)) * 255)
    .toString(16)
    .padStart(2, "0");
  return `${base}${hex}`;
};

/* Exact hex-suffix equivalents of the alpha values already in use, so a
 * migrating component can express intent without recomputing the suffix.
 * Each maps to the two-character suffix it replaces. */
export const ALPHA = {
  faint:  0.016,  // "04"
  ghost:  0.027,  // "07"
  hint:   0.031,  // "08"
  subtle: 0.047,  // "0c"
  soft:   0.078,  // "14"
  low:    0.094,  // "18"
  medium: 0.133,  // "22"
  strong: 0.267,  // "44"
  heavy:  0.333,  // "55"
};

/* ── Typography ───────────────────────────────────────────────────────────
 * Nine distinct sizes are in use, but three of them (9, 11, 10) account for
 * ~81% of all 208 occurrences — this is already close to a scale, it was
 * just never named.
 */
export const type = {
  size: {
    xs:   9,    // badges, stat labels, dense metadata — the most used size
    sm:   10,
    md:   11,
    base: 12,
    lg:   13,
    xl:   14,
    xxl:  15,
    h2:   16,
    h1:   18,
  },
  weight: {
    medium:   500,
    semibold: 600,
    bold:     700,
    black:    900,
  },
  /* Letter-spacing. This UI leans heavily on wide tracking for its
   * terminal-ish look, so these carry real design intent. */
  tracking: {
    tight:  "0.03em",
    snug:   "0.06em",
    normal: "0.08em",
    wide:   "0.1em",    // most common
    wider:  "0.12em",
    widest: "0.14em",
    ultra:  "0.16em",
    max:    "0.18em",   // logo / section headers
  },
  /* Every component inherits the app font; kept as a token so a theme
   * could later change it without touching components. */
  family: "inherit",
};

/* ── Radius ───────────────────────────────────────────────────────────────
 * Only three values exist across the entire app. The near-total absence of
 * rounding is a deliberate part of the visual identity, not an oversight —
 * worth preserving as an explicit token so nobody "helpfully" rounds
 * things later.
 */
export const radius = {
  none: 0,
  pill: 11,
  full: "50%",
};

/* ── Spacing ──────────────────────────────────────────────────────────────
 * The systematised scale. Gaps already cluster tightly around these values.
 */
export const space = {
  none: 0,
  hair: 2,
  xxs:  4,
  xs:   6,
  sm:   8,
  md:   10,
  lg:   12,
  xl:   16,
  xxl:  20,
  "3xl": 24,
  "4xl": 28,
};

/* ── Legacy spacing ───────────────────────────────────────────────────────
 * Values that do NOT fit the scale above. Each is a one-off from before
 * spacing was systematised — 59 distinct padding shorthands exist across
 * the app, mostly near-duplicates like 6px/8px/9px/10px vertical.
 *
 * They are named and centralised here rather than left inline for one
 * reason: it turns "normalise the spacing scale" from a 41-file edit into a
 * single-file edit. Phase 2 decides whether to snap these to `space`
 * (a 1–2px visual change in places) or keep them exact. Until then they
 * preserve current rendering precisely.
 */
export const legacy = {
  badgePadY:   1,    // ActionBadge / StatusBadge vertical
  badgePadX:   6,
  btnPadY:     5,    // Btn
  btnPadX:     13,
  emptyPadY:   38,   // EmptyState
  emptyPadX:   16,
  statGapY:    3,    // Stat label → value
  ledSize:     7,    // default LED diameter
  ledGlow:     5,    // box-shadow blur
  ledGlowFar:  10,

  /* ActionBadge's fallback background for an unrecognised action type.
   * Kept as the original literal rather than folded into palette.card
   * (#0d0f14) — they are NOT the same colour, and swapping it would be a
   * real visual change smuggled into a no-op refactor. */
  badgeFallbackBg: "#111",
};
