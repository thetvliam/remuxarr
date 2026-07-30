import { useTheme } from "../../theme";

// Small action button
export const Btn = ({ label, color, bg, onClick, disabled }) => {
    const { palette, type, space, radius } = useTheme();
    return (
        <button
        onClick={onClick}
        disabled={disabled}
        style={{
            padding: `${space.xs}px ${space.lg}px`,
            background: bg || "transparent",
            border: `1px solid ${disabled ? palette.dim : color}`,
            borderRadius: radius.sm,
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
};
