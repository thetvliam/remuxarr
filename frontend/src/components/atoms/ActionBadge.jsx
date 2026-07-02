import { C, ACTION_CFG } from "../../constants";

// Action type badge (COPY / DROP / TRANSCODE / CONVERT / FLAG / EXTRACT / FASTSTART)
export const ActionBadge = ({ type }) => {
    const cfg = ACTION_CFG[type] || { bg: "#111", border: C.border, text: C.dim, label: (type || "?").toUpperCase() };
    return (
        <span style={{
            display: "inline-block",
            padding: "1px 6px",
            background: cfg.bg,
            border: `1px solid ${cfg.border}`,
            color: cfg.text,
            fontSize: 9,
            fontFamily: "inherit",
            letterSpacing: "0.1em",
            fontWeight: 700,
            flexShrink: 0,
        }}>
        {cfg.label}
        </span>
    );
};
