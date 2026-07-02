// Coloured status LED
export const LED = ({ color, pulse = false, size = 7 }) => (
    <span style={{
        display: "inline-block",
        width: size, height: size,
        borderRadius: "50%",
        background: color,
        flexShrink: 0,
        boxShadow: pulse ? `0 0 5px ${color}, 0 0 10px ${color}55` : "none",
        animation: pulse ? "ledPulse 2s ease-in-out infinite" : "none",
    }} />
);
