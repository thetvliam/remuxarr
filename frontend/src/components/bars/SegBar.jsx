import { useTheme } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * SEGMENTED PROGRESS BAR  (VU-meter aesthetic)
 * Colour shifts green → amber → red as it fills up.
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */
export const SegBar = ({ value = 0, segments = 50, height }) => {
    const { palette, space, size } = useTheme();
    const px     = height ?? size.segBarHeight;
    const filled = Math.round((Math.min(100, value) / 100) * segments);
    return (
        <div style={{ display: "flex", gap: space.hair }}>
        {Array.from({ length: segments }, (_, i) => {
            const on    = i < filled;
            const frac  = i / segments;
            const color = on
            ? frac > 0.86 ? palette.red
            : frac > 0.62 ? palette.amber
            : palette.green
            : palette.border;
            return (
                <div
                key={i}
                style={{ flex: 1, height: px, background: color, transition: "background 0.06s" }}
                />
            );
        })}
        </div>
    );
};
