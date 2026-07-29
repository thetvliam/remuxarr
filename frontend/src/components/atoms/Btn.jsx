import { palette, type, legacy } from "../../theme";

// Small action button
export const Btn = ({ label, color, bg, onClick, disabled }) => (
    <button
    onClick={onClick}
    disabled={disabled}
    style={{
        padding: `${legacy.btnPadY}px ${legacy.btnPadX}px`,
        background: bg || "transparent",
        border: `1px solid ${disabled ? palette.dim : color}`,
        color: disabled ? palette.dim : color,
        fontSize: type.size.xs,
        fontFamily: type.family,
        fontWeight: type.weight.bold,
        letterSpacing: type.tracking.normal,
        cursor: disabled ? "not-allowed" : "pointer",
    }}
    >
    {label}
    </button>
);
