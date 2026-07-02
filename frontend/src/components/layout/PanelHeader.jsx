import { C } from "../../constants";
import { fmtCount } from "../../utils";

/* ═══════════════════════════════════════════════════════════════════════════
 * PANEL HEADER
 * count can be a number or a pre-formatted string (e.g. "3/47" for a
 * filtered queue view).  Numbers ≥ 1000 are abbreviated (19k, 19.5k) and
 * receive a native title tooltip with the exact localised value.
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
export const PanelHeader = ({ label, count, right }) => {
    const isNum      = typeof count === "number";
    const display    = isNum ? fmtCount(count) : (count ?? "");
    const tooltip    = isNum && count >= 1000 ? count.toLocaleString() + " items" : undefined;

    return (
        <div style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "7px 14px",
            background: C.card,
            borderBottom: `1px solid ${C.border}`,
            flexShrink: 0,
        }}>
        <span style={{ color: C.dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>
        {label}
        </span>
        <span
        title={tooltip}
        style={{
            padding: "0 5px",
            border: `1px solid ${C.border}`,
            color: C.muted,
            fontSize: 9,
            cursor: tooltip ? "default" : undefined,
        }}
        >
        {display}
        </span>
        {right && <div style={{ marginLeft: "auto" }}>{right}</div>}
        </div>
    );
};
