import { ACTION_CFG } from "../../constants";
import { palette, type, legacy } from "../../theme";

// Action type badge (COPY / DROP / TRANSCODE / CONVERT / FLAG / EXTRACT / FASTSTART)
export const ActionBadge = ({ type: actionType }) => {
    const cfg = ACTION_CFG[actionType] || {
        bg: legacy.badgeFallbackBg, border: palette.border, text: palette.dim,
        label: (actionType || "?").toUpperCase(),
    };
    return (
        <span style={{
            display: "inline-block",
            padding: `${legacy.badgePadY}px ${legacy.badgePadX}px`,
            background: cfg.bg,
            border: `1px solid ${cfg.border}`,
            color: cfg.text,
            fontSize: type.size.xs,
            fontFamily: type.family,
            letterSpacing: type.tracking.wide,
            fontWeight: type.weight.bold,
            flexShrink: 0,
        }}>
        {cfg.label}
        </span>
    );
};
