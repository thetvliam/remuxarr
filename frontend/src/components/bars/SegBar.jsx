import { C } from "../../constants";

/* ═══════════════════════════════════════════════════════════════════════════
 * SEGMENTED PROGRESS BAR  (VU-meter aesthetic)
 * Colour shifts green → amber → red as it fills up.
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
export const SegBar = ({ value = 0, segments = 50, height = 13 }) => {
    const filled = Math.round((Math.min(100, value) / 100) * segments);
    return (
        <div style={{ display: "flex", gap: 2 }}>
        {Array.from({ length: segments }, (_, i) => {
            const on    = i < filled;
            const frac  = i / segments;
            const color = on
            ? frac > 0.86 ? C.red
            : frac > 0.62 ? C.amber
            : C.green
            : C.border;
            return (
                <div
                key={i}
                style={{ flex: 1, height, background: color, transition: "background 0.06s" }}
                />
            );
        })}
        </div>
    );
};
