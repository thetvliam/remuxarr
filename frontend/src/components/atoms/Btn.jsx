import { C } from "../../constants";

// Small action button
export const Btn = ({ label, color, bg, onClick, disabled }) => (
    <button
    onClick={onClick}
    disabled={disabled}
    style={{
        padding: "5px 13px",
        background: bg || "transparent",
        border: `1px solid ${disabled ? C.dim : color}`,
        color: disabled ? C.dim : color,
        fontSize: 9,
        fontFamily: "inherit",
        fontWeight: 700,
        letterSpacing: "0.08em",
        cursor: disabled ? "not-allowed" : "pointer",
    }}
    >
    {label}
    </button>
);
