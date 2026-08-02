import { useTheme, alpha, ALPHA } from "../../theme";

// Coloured status LED. The `size` prop still accepts an explicit override;
// when the caller doesn't pass one it comes from the theme, so a roomier
// theme gets a proportionally larger indicator.
//
// The prop is aliased because it collides with the `size` token group —
// same convention as ActionBadge's `{ type: actionType }`, which collides
// with the `type` group. The prop is renamed rather than the token, so the
// token keeps its canonical name everywhere and callers are unaffected.
export const LED = ({ color, pulse = false, size: sizeOverride }) => {
    const { radius, size } = useTheme();
    const px = sizeOverride ?? size.ledSize;
    return (
        <span style={{
            display: "inline-block",
            width: px, height: px,
            borderRadius: radius.full,
            background: color,
            flexShrink: 0,
            boxShadow: pulse
                ? `0 0 ${size.ledGlow}px ${color}, 0 0 ${size.ledGlowFar}px ${alpha(color, ALPHA.heavy)}`
                : "none",
            animation: pulse ? "ledPulse 2s ease-in-out infinite" : "none",
        }} />
    );
};
