import { useTheme, alpha, ALPHA } from "../../theme";

// Status pill badge
export const StatusBadge = ({ status }) => {
    const { palette, type, legacy, statusColor } = useTheme();
    const color = statusColor[status] || palette.dim;
    return (
        <span style={{
            display: "inline-block",
            padding: `${legacy.badgePadY}px ${legacy.badgePadX}px`,
            border: `1px solid ${alpha(color, ALPHA.strong)}`,
            borderRadius: legacy.badgeRadius,
            color,
            fontSize: type.size.xs,
            fontFamily: type.family,
            letterSpacing: type.tracking.wide,
            fontWeight: type.weight.bold,
        }}>
        {(status || "").replace(/_/g, " ").toUpperCase()}
        </span>
    );
};
