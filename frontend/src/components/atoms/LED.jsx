import { useTheme, alpha, ALPHA } from "../../theme";

// Coloured status LED. `size` still accepts an explicit override; when the
// caller doesn't pass one it comes from the theme, so a roomier theme gets
// a proportionally larger indicator.
export const LED = ({ color, pulse = false, size }) => {
    const { radius, legacy } = useTheme();
    const px = size ?? legacy.ledSize;
    return (
        <span style={{
            display: "inline-block",
            width: px, height: px,
            borderRadius: radius.full,
            background: color,
            flexShrink: 0,
            boxShadow: pulse
                ? `0 0 ${legacy.ledGlow}px ${color}, 0 0 ${legacy.ledGlowFar}px ${alpha(color, ALPHA.heavy)}`
                : "none",
            animation: pulse ? "ledPulse 2s ease-in-out infinite" : "none",
        }} />
    );
};
