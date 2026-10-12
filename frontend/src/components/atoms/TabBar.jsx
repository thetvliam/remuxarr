import { useTheme } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * TAB BAR
 * A full-width row of tabs that chooses which panel is shown, underlined
 * when selected. Used where two side-by-side panels have no room on a phone
 * and become one panel at a time: the dashboard's Queue and History, and
 * Forge's Candidates and Processed. Both were written out separately, line
 * for line the same.
 *
 * The header's page nav looks similar and is not this: it navigates between
 * pages, marks the current one with aria-current, and colours itself for
 * review alerts. Nor is the settings category list, which is vertical.
 *
 * Each side of a tab's border is set on its own rather than as `border:
 * "none"` plus a bottom edge. That pairing behaved here, because the
 * shorthand never changed, but it is the shape that broke the segmented
 * controls (see SegmentedControl), and keeping to longhands removes the
 * question.
 *
 * aria-pressed marks the selected tab, as SegmentedControl does; until now
 * only its colour and underline said which panel was showing.
 ═══════════════════════════════════════════════════════════════════════════ */

export const TabBar = ({ tabs, value, onChange, label }) => {
  const { palette, type, space, size } = useTheme();

  return (
    <div role="group" aria-label={label} style={{
      display: "flex",
      flexShrink: 0,
      borderBottom: `1px solid ${palette.border}`,
      background: palette.card,
    }}>
    {tabs.map(tab => {
      const on = tab.value === value;
      return (
        <button
        key={tab.value}
        type="button"
        onClick={() => onChange(tab.value)}
        aria-pressed={on}
        style={{
          flex: 1,
          padding: `${space.md}px 0`,
          background: "transparent",
          borderTop: "none",
          borderRight: "none",
          borderLeft: "none",
          borderBottom: `${size.accentThin}px solid ${on ? palette.amber : "transparent"}`,
          color: on ? palette.amber : palette.dim,
          fontSize: type.size.xs,
          fontFamily: type.family,
          letterSpacing: type.tracking.widest,
          fontWeight: type.weight.bold,
          cursor: "pointer",
        }}
        >
        {tab.label}
        </button>
      );
    })}
    </div>
  );
};
