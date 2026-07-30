import { useTheme } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * MINI PROGRESS BAR  (used inside queue rows while processing)
 ═ * * ═*═════════════════════════════════════════════════════════════════════════ */
export const MiniBar = ({ value = 0, segments = 28 }) => {
    const { palette, space, legacy } = useTheme();
    const filled = Math.round((value / 100) * segments);
    return (
        <div style={{ display: "flex", gap: space.hair }}>
        {Array.from({ length: segments }, (_, i) => (
            <div
            key={i}
            style={{
                flex: 1,
                height: legacy.barHeight,
                background: i < filled ? palette.blue : palette.border,
                transition: "background 0.06s",
            }}
            />
        ))}
        </div>
    );
};
