import { radius, legacy, alpha, ALPHA } from "../../theme";

// Coloured status LED
export const LED = ({ color, pulse = false, size = legacy.ledSize }) => (
    <span style={{
        display: "inline-block",
        width: size, height: size,
        borderRadius: radius.full,
        background: color,
        flexShrink: 0,
        boxShadow: pulse
            ? `0 0 ${legacy.ledGlow}px ${color}, 0 0 ${legacy.ledGlowFar}px ${alpha(color, ALPHA.heavy)}`
            : "none",
        animation: pulse ? "ledPulse 2s ease-in-out infinite" : "none",
    }} />
);
