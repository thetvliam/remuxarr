import { useTheme } from "../../theme";
import { useBreakpoint } from "../../hooks/useBreakpoint";

/* ── Action row ─────────────────────────────────────────────────────────────
 * A title and description on the left and the thing that acts on them on the
 * right: a button, or a switch. Settings' action sections (Backup & Restore,
 * Full Database Backup & Restore, Danger Zone, Maintenance) each wrote this
 * row out by hand, nine times, with the same title and description styles
 * copied into each and three different rules for a narrow screen.
 *
 * On a phone the action goes below the text, always, as a schema setting's
 * control goes below its label (FieldRow), and on the same breakpoint as
 * every other layout decision (useBreakpoint's isMobile). It used to be left
 * to the row's width instead: Danger Zone's and Maintenance's rows wrapped
 * only when the button no longer fitted beside a 220px description, and the
 * Backup rows never wrapped at all. Measured in Chromium, that left one page
 * mixed at ordinary phone widths: at 360 to 390px EXPORT and IMPORT… sat
 * beside descriptions of 192 to 258px while CLEAR DATABASE sat below its
 * own, and at 320px the Backup descriptions were squeezed to as little as
 * 152px (soft theme). Stacked, every description has the full width.
 *
 * `children` follow the description in the text column (a checkbox, a
 * warning, a result line). `divider` draws the rule between rows that sit
 * one after another; `flush` drops the vertical padding for a row inside a
 * card, whose own padding already spaces it.
 */
export const ActionRow = ({ title, description, children, action, divider = false, flush = false }) => {
  const { palette, type, space } = useTheme();
  const { isMobile } = useBreakpoint();
  return (
    <div style={{
      display: "flex",
      flexDirection: isMobile ? "column" : "row",
      alignItems: "flex-start",
      gap: isMobile ? space.md : space.huge,
      padding: flush ? 0 : `${space.xl}px 0`,
      ...(divider && { borderBottom: `1px solid ${palette.border}` }),
    }}>
    {/* Stretched so that, stacked, the text keeps the full width rather
      * than shrinking to its content above a left-aligned button. */}
    <div style={{ flex: isMobile ? "none" : 1, minWidth: 0, alignSelf: "stretch" }}>
    {title && (
      <div style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.semibold, marginBottom: space.xs }}>
      {title}
      </div>
    )}
    {description && (
      <div style={{ color: palette.muted, fontSize: type.size.md, lineHeight: type.leading.relaxed }}>
      {description}
      </div>
    )}
    {children}
    </div>
    {action}
    </div>
  );
};
