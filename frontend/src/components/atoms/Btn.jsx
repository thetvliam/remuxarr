import { useTheme } from "../../theme";

// Small action button. `pressed` marks a button that holds a state rather
// than firing an action once — a chosen option among several — so a reader
// and a test can both tell which one is selected.
export const Btn = ({ label, color, bg, onClick, disabled, pressed }) => {
    const { palette, type, space, radius } = useTheme();
    return (
        <button
        onClick={onClick}
        disabled={disabled}
        aria-pressed={pressed}
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
