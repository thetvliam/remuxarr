import { useTheme } from "../../theme";

/* ── Section heading ────────────────────────────────────────────────────────
 * Every section heading on the Settings page, the schema groups' and the
 * action sections' alike. Each action section used to draw its own, six
 * copies of one style in six files, and they had drifted: Backup & Restore
 * and Full Database Backup & Restore were grey where every other heading is
 * amber, with nothing recording why. They also opened with a top margin and
 * a rule written for the single long page they were built on, before
 * Settings was split into categories. Alone on their own page, that put the
 * first heading 65px below the save bar with the rule above it, where every
 * other page has it 6px below with the rule underneath (measured in
 * Chromium, terminal theme).
 *
 * `first` marks the first heading on its page, which only the page knows:
 * SettingsPage passes it, so a section never assumes where it sits. `tone`
 * replaces the amber for a heading that has to stand apart; Danger Zone is
 * the only one.
 *
 * A heading to assistive technology as well, at level 2. None of the page's
 * section titles was one, so a screen reader could not move from section
 * to section. The role rather than an <h2>, so there are no browser heading
 * styles to undo. */
export const SectionHeader = ({ label, first = false, tone }) => {
  const { palette, type, space } = useTheme();
  return (
    <div
    role="heading"
    aria-level={2}
    style={{
      display: "flex",
      alignItems: "center",
      gap: space.md,
      margin: first ? `${space.xxs}px 0 0` : `${space.xxxl}px 0 0`,
      paddingBottom: space.sm,
      borderBottom: `1px solid ${palette.border}`,
    }}
    >
    <span style={{ color: tone ?? palette.amber, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold }}>
    {label.toUpperCase()}
    </span>
    </div>
  );
};
