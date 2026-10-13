import { useTheme } from "../../theme";
import { fmtCount } from "../../utils";

/* ═══════════════════════════════════════════════════════════════════════════
 * PANEL HEADER
 * count can be a number or a pre-formatted string (e.g. "3/47" for a
 * filtered queue view).  Numbers ≥ 1000 are abbreviated (19k, 19.5k) and
 * receive a native title tooltip with the exact localised value.
 *
 * `below` is a second row under the title, across the panel's full width,
 * for controls that do not fit beside it: History's tabs. The row wraps only
 * when there is one, so a header without it is unchanged.
 ═══════════════════════════════════════════════════════════════════════════ */
export const PanelHeader = ({ label, count, right, below }) => {
    const { palette, type, space, radius } = useTheme();
    const isNum   = typeof count === "number";
    const display = isNum ? fmtCount(count) : (count ?? "");
    const tooltip = isNum && count >= 1000 ? count.toLocaleString() + " items" : undefined;

    return (
        <div style={{
            display: "flex",
            alignItems: "center",
            flexWrap: below ? "wrap" : undefined,
            gap: space.sm,
            padding: `${space.sm}px ${space.xl}px`,
            background: palette.card,
            borderBottom: `1px solid ${palette.border}`,
            flexShrink: 0,
        }}>
        <span style={{
            color: palette.dim,
            fontSize: type.size.xs,
            letterSpacing: type.tracking.max,
            fontWeight: type.weight.bold,
        }}>
        {label}
        </span>
        <span
        title={tooltip}
        style={{
            padding: `0 ${space.xs}px`,
            border: `1px solid ${palette.border}`,
            borderRadius: radius.sm,
            color: palette.muted,
            fontSize: type.size.xs,
            cursor: tooltip ? "default" : undefined,
        }}
        >
        {display}
        </span>
        {right && <div style={{ marginLeft: "auto" }}>{right}</div>}
        {below && <div style={{ flexBasis: "100%" }}>{below}</div>}
        </div>
    );
};
