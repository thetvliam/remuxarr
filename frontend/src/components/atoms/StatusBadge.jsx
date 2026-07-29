import { STATUS_COLOR } from "../../constants";
import { palette, type, legacy, alpha, ALPHA } from "../../theme";

// Status pill badge
export const StatusBadge = ({ status }) => {
    const color = STATUS_COLOR[status] || palette.dim;
    return (
        <span style={{
            display: "inline-block",
            padding: `${legacy.badgePadY}px ${legacy.badgePadX}px`,
            border: `1px solid ${alpha(color, ALPHA.strong)}`,
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
