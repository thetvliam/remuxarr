import { useTheme, alpha, ALPHA } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * HEADER CONTROLS
 * DRY RUN, AUTO / MANUAL, PAUSE / RESUME and SCAN, each defined once for both
 * of AppHeader's layouts. They used to be written out twice, in the desktop
 * bar and again in the phone layout, so a change to how one looked in a
 * state had to be made in two places far apart in the file, and nothing
 * checked that it had been.
 *
 * Where a control is drawn is its form:
 *   bar    the desktop header, full or compact
 *   row    a full-width row in the phone drawer (the three toggles)
 *   phone  the phone's always-visible top row (SCAN)
 * A toggle's look in each state is decided once, in headerToggles, and its
 * form decides only its size, its alignment and its text. SCAN's look is
 * decided once as well, in ScanButton; its forms differ in size, text and
 * titles.
 * ═══════════════════════════════════════════════════════════════════════════ */

/**
 * The three toggles, as data. `lit` is the state drawn highlighted, which
 * for AUTO is MANUAL: the departure from the default, not the on state.
 * `pressed` is the toggle's own on state, which both forms report through
 * aria-pressed because colour and glyph alone do not reach anyone not
 * seeing them. The drawer rows say their state in words as well; they had
 * only the words until the controls were defined here, and a screen reader
 * did not announce them as toggles at all.
 */
export const headerToggles = ({
  palette,
  dryRun, onToggleDryRun,
  autoStart, onToggleAutoStart,
  workerPaused, onTogglePause,
}) => [
  {
    key: "dryRun",
    onToggle: onToggleDryRun,
    pressed: dryRun,
    lit: dryRun, tone: palette.yellow, wash: ALPHA.mild,
    full: (dryRun ? "◆" : "◇") + " DRY RUN",
    compact: dryRun ? "◆" : "◇",
    compactName: "Dry run",
    compactTitle: `Dry run ${dryRun ? "on" : "off"}`,
    row: dryRun ? "◆ DRY RUN  — tap to disable" : "◇ DRY RUN  — tap to enable",
  },
  {
    key: "autoStart",
    onToggle: onToggleAutoStart,
    pressed: autoStart,
    lit: !autoStart, tone: palette.blue, wash: ALPHA.low,
    full: autoStart ? "⚡ AUTO" : "⏸ MANUAL",
    // Compact drops the glyph and keeps the word: MANUAL's glyph is the
    // same ⏸ as PAUSE beside it, so glyphs alone would show two identical
    // buttons in the usual state of manual start, running.
    compact: autoStart ? "AUTO" : "MANUAL",
    title: autoStart
      ? "Auto-start enabled — files process as soon as a scan or an import queues them"
      : "Auto-start disabled — files from a scan or an import wait until you click Resume",
    row: autoStart ? "⚡ AUTO-START  — tap to disable" : "⏸ MANUAL  — tap to enable auto-start",
  },
  {
    key: "pause",
    onToggle: onTogglePause,
    pressed: workerPaused,
    lit: workerPaused, tone: palette.yellow, wash: ALPHA.mild, pulse: true,
    full: workerPaused ? "▶ RESUME" : "⏸ PAUSE",
    compact: workerPaused ? "▶" : "⏸",
    compactName: workerPaused ? "Resume" : "Pause",
    title: workerPaused ? "Resume processing" : "Pause processing — finish the current job then stop",
    row: workerPaused ? "▶ RESUME  — tap to resume processing" : "⏸ PAUSE  — tap to pause after current job",
  },
];

/**
 * One toggle, drawn for a form. Both forms carry its state in aria-pressed.
 * In the compact bar it also carries its full meaning in its name and
 * tooltip; a drawer row needs neither, because its text spells it out.
 */
export const HeaderToggle = ({ toggle: t, form, compact = false, onClick }) => {
  const { palette, type, space, radius } = useTheme();
  const bar = form === "bar";
  return (
    <button
    onClick={onClick}
    aria-pressed={t.pressed}
    aria-label={bar && compact ? t.compactName : undefined}
    title={bar ? (compact ? t.compactTitle ?? t.title : t.title) : undefined}
    style={{
      ...(bar
        ? { padding: `${space.xxs}px ${space.md}px`, marginRight: space.sm, fontSize: type.size.xs }
        : { padding: `${space.md}px ${space.xl}px`, textAlign: "left", fontSize: type.size.sm }),
      background: t.lit ? alpha(t.tone, t.wash) : "transparent",
      border: `1px solid ${t.lit ? t.tone : palette.border}`,
      borderRadius: radius.sm,
      color: t.lit ? t.tone : palette.dim,
      fontFamily: type.family,
      letterSpacing: type.tracking.wide,
      cursor: "pointer",
      ...(t.pulse && { animation: t.lit ? "ledPulse 2s ease-in-out infinite" : "none" }),
    }}
    >
    {bar ? (compact ? t.compact : t.full) : t.row}
    </button>
  );
};

/**
 * SCAN, which is also the stop button while a scan runs and shows its
 * progress. Its look is decided once, here, for both forms: ↻ when idle,
 * red and tinted while a scan runs as the bar's other lit controls are,
 * and the toggles' regular weight and wide letter spacing. Each form adds
 * only its size and its place in the row.
 *
 * The two forms were written out separately and had drifted apart: the
 * phone showed ⟳ and a bold, tighter label, and the bar alone stayed
 * untinted while scanning. Nothing recorded any of it as a choice.
 *
 * ↻ because neither bundled font has ↻ or ⟳, so each device draws the
 * arrow from its own fonts, and ↻ is in the main Arrows block, which more
 * fonts cover than the supplementary block ⟳ is in. The bar's weight and
 * spacing rather than the phone's because the bar's widths are what the
 * 1140px and 860px breakpoints were measured against (AppHeader's header
 * comment has the figures). The bold label was not needed for fit:
 * measured in Chromium during a 12345/12345 scan, the phone's top row fits
 * at 320px in every theme with either look, the button within 2px.
 *
 * The phone's padding is narrower, which is its size rather than its look.
 * Its titles are its own as well; a tooltip never shows on touch, so they
 * matter little either way.
 *
 * The bar's compact form and the phone both show progress as "✕ 123/456",
 * without STOP and the brackets; the compact bar keeps the full meaning in
 * the button's name and tooltip.
 */
export const ScanButton = ({ form, compact = false, scanning, scanProgress, onTriggerScan, onCancelScan }) => {
  const { palette, type, space, radius } = useTheme();
  const short = scanProgress && `✕ ${scanProgress.scanned}/${scanProgress.total}`;
  const onClick = scanning ? onCancelScan : onTriggerScan;
  const look = {
    background: scanning ? alpha(palette.red, ALPHA.low) : "transparent",
    border: `1px solid ${scanning ? palette.red : palette.border}`,
    borderRadius: radius.sm,
    color: scanning ? palette.red : palette.dim,
    fontSize: type.size.xs, fontFamily: type.family, letterSpacing: type.tracking.wide,
    cursor: "pointer",
    animation: scanning ? "ledPulse 1.5s ease-in-out infinite" : "none",
  };

  if (form === "phone") {
    return (
      <button
      onClick={onClick}
      title={scanning ? "Cancel the running scan" : "Scan library now"}
      style={{ ...look, flexShrink: 0, padding: `${space.xxs}px ${space.sm}px`, whiteSpace: "nowrap" }}
      >
      {scanning ? (short || "✕ STOP") : "↻ SCAN"}
      </button>
    );
  }

  const full = scanning
    ? (scanProgress ? `✕ STOP (${scanProgress.scanned}/${scanProgress.total})` : "✕ STOP SCAN")
    : "↻ SCAN";
  const shortened = scanning && scanProgress && compact;
  return (
    <button
    onClick={onClick}
    aria-label={shortened
      ? `Stop scan (${scanProgress.scanned} of ${scanProgress.total} scanned)` : undefined}
    title={shortened ? full : undefined}
    style={{ ...look, padding: `${space.xxs}px ${space.lg}px`, marginRight: space.xl }}
    >
    {shortened ? short : full}
    </button>
  );
};
