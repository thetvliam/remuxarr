import { C, STATUS_COLOR } from "../../constants";

// Status pill badge
export const StatusBadge = ({ status }) => {
    const color = STATUS_COLOR[status] || C.dim;
    return (
        <span style={{
            display: "inline-block",
            padding: "1px 6px",
            border: `1px solid ${color}44`,
            color,
            fontSize: 9,
            fontFamily: "inherit",
            letterSpacing: "0.1em",
            fontWeight: 700,
        }}>
        {(status || "").replace(/_/g, " ").toUpperCase()}
        </span>
    );
};
