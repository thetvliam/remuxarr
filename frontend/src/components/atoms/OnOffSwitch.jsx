import { useTheme, alpha, ALPHA } from "../../theme";

/* ── On/off switch ──────────────────────────────────────────────────────────
 * The ■ ON / □ OFF button every on/off setting on the Settings page uses,
 * shared so that the page has one on/off control. It was written inside
 * SettingInput, and two other places drew their own: Maintenance's two
 * switches were an amber sliding knob, and the two "Include connection
 * secrets" options in Backup were browser checkboxes. This is the one that
 * sixteen settings already used.
 *
 * A switch, not a button: role plus aria-checked is what conveys on/off. It
 * was announced as an unlabelled button whose only state cue was the ■/□
 * glyph in its text. `label` is its name, since the visible label is always
 * somewhere else.
 *
 * It never wraps or shrinks, like Btn's md size: it sits beside text that
 * is allowed to, in a FieldRow, an ActionRow or a line of its own.
 */
export const OnOffSwitch = ({ on, onChange, label }) => {
  const { palette, type, space, radius } = useTheme();
  return (
    <button
    type="button"
    role="switch"
    aria-checked={on}
    aria-label={label}
    onClick={() => onChange(!on)}
    style={{
      padding: `${space.xs}px ${space.xl}px`,
      background: on ? alpha(palette.green, ALPHA.low) : "transparent",
      border: `1px solid ${on ? palette.green : palette.border}`,
      borderRadius: radius.sm,
      color: on ? palette.green : palette.dim,
      fontSize: type.size.sm,
      fontFamily: type.family,
      letterSpacing: type.tracking.wide,
      whiteSpace: "nowrap",
      flexShrink: 0,
      cursor: "pointer",
    }}
    >
    {on ? "■ ON" : "□ OFF"}
    </button>
  );
};
