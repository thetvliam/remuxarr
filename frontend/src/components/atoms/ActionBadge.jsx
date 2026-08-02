import { useTheme } from "../../theme";

// Action type badge (COPY / DROP / TRANSCODE / CONVERT / FLAG / EXTRACT / FASTSTART)
// NOTE: the incoming prop is destructured as `actionType` because `type` is
// also the name of the theme's typography token — a collision worth knowing
// about, since several components take a `type` prop. The external API is
// unchanged: callers still write <ActionBadge type={...} />.
export const ActionBadge = ({ type: actionType }) => {
    const { palette, type, space, radius, surface, actionCfg } = useTheme();
    const cfg = actionCfg[actionType] || {
        bg: surface.badgeFallbackBg,
        border: palette.border,
        text: palette.dim,
        label: (actionType || "?").toUpperCase(),
    };
    return (
        <span style={{
            display: "inline-block",
            padding: `${space.hair}px ${space.xs}px`,
            background: cfg.bg,
            border: `1px solid ${cfg.border}`,
            borderRadius: radius.none,
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
