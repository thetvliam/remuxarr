import { useTheme, alpha, ALPHA } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * SEGMENTED CONTROL
 * A strip of buttons of which one is selected: History's tabs, the log
 * viewer's level filter. Each option carries its own colour, and may carry
 * a badge (a count) and a tooltip.
 *
 * Borders are set side by side and never with the `border` shorthand. The
 * two controls this replaces each set the shorthand and then borderRight:
 * "none" on the same element, so neighbours could share an edge. React
 * applies both on the first render, which left the selected segment without
 * a right edge; when the selection changed, only the shorthand's value had
 * changed, so React re-applied the shorthand alone, which resets every side,
 * and the edge came back with the borders doubled. Longhands have no such
 * interaction: each is set, or re-set, on its own.
 *
 * Neighbours share an edge by overlapping instead: every segment has all four
 * sides and each after the first is pulled 1px left over the one before. The
 * selected segment is raised above its neighbours, so its colour shows on
 * all four sides rather than being covered on the right by the next one's
 * neutral edge. Only the strip's outer corners take the theme's radius; a
 * rounded inner corner would open a gap in the shared edge.
 *
 * aria-pressed marks the selected segment, the convention Btn and the header
 * already use, so the selection is not conveyed by colour alone.
 *
 * `fill` stretches the strip across its container in equal segments, for a
 * strip given a row of its own (History's tabs, under the panel title). Each
 * label stays on one line, and a badge drops beneath its label when the
 * segment is too narrow for both. Without it a squeezed segment broke its
 * label instead ("DRY / RUN"), and at its narrowest the strip ran out of the
 * panel. Without `fill` the strip is as it was, which is what the log
 * viewer's level filter uses.
 ═══════════════════════════════════════════════════════════════════════════ */

export const SegmentedControl = ({ options, value, onChange, label, fill = false }) => {
  const { palette, type, space, radius } = useTheme();
  const last = options.length - 1;

  return (
    <div role="group" aria-label={label} style={fill ? { display: "flex", width: "100%" } : { display: "flex" }}>
    {options.map((option, i) => {
      const on    = option.value === value;
      const color = option.color || palette.muted;
      const edge  = `1px solid ${on ? color : palette.border}`;
      return (
        <button
        key={option.value}
        type="button"
        onClick={() => onChange(option.value)}
        aria-pressed={on}
        title={option.title}
        style={{
          position: "relative",
          zIndex: on ? 1 : 0,
          flex: fill ? "1 1 0" : undefined,
          marginLeft: i === 0 ? 0 : -1,
          padding: `${space.hair}px ${space.md}px`,
          background: on ? alpha(color, ALPHA.low) : "transparent",
          borderTop: edge,
          borderRight: edge,
          borderBottom: edge,
          borderLeft: edge,
          borderTopLeftRadius:     i === 0 ? radius.sm : 0,
          borderBottomLeftRadius:  i === 0 ? radius.sm : 0,
          borderTopRightRadius:    i === last ? radius.sm : 0,
          borderBottomRightRadius: i === last ? radius.sm : 0,
          color: on ? color : palette.dim,
          fontSize: type.size.xs,
          fontFamily: type.family,
          letterSpacing: type.tracking.wide,
          cursor: "pointer",
        }}
        >
        {fill ? <span style={{ whiteSpace: "nowrap" }}>{option.label}</span> : option.label}
        {option.badge != null && (
          <span style={fill
            ? { marginLeft: space.xs, color, display: "inline-block", whiteSpace: "nowrap" }
            : { marginLeft: space.xs, color }}>{option.badge}</span>
        )}
        </button>
      );
    })}
    </div>
  );
};
