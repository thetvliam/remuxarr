import { useTheme } from "../../theme";

// Small action button. `pressed` marks a button that holds a state rather
// than firing an action once — a chosen option among several — so a reader
// and a test can both tell which one is selected.
//
// Two sizes. "sm", the default, is the compact one. "md" is the larger one
// each Settings section uses for its own action (EXPORT, CLEAR DATABASE, RUN
// CLEANUP NOW and the rest), which used to be written out in full at every
// one of them. It never wraps or shrinks, because most of those buttons share
// a row with a description that is allowed to. Measured in Chromium, without
// either rule the settings import's CLICK AGAIN TO CONFIRM wraps onto two
// lines on a 320px phone in linen.
//
// Disabled differs by size. An sm button greys itself. An md button keeps the
// colour its caller passes, because the Settings actions were not consistent
// and moving them here was agreed to leave their colours alone: the checks
// turn muted while their request runs, and so does an action with nothing to
// act on, but the destructive ones stay red while theirs runs. Both sizes
// show the not-allowed cursor.
export const Btn = ({ label, color, bg, onClick, disabled, pressed, size = "sm" }) => {
    const { palette, type, space, radius } = useTheme();
    const md = size === "md";
    const shown = disabled && !md ? palette.dim : color;
    const sized = md
        ? { padding: `${space.xs}px ${space.xl}px`, fontSize: type.size.sm,
            letterSpacing: type.tracking.wide, whiteSpace: "nowrap", flexShrink: 0 }
        : { padding: `${space.xs}px ${space.lg}px`, fontSize: type.size.xs,
            letterSpacing: type.tracking.normal };
    return (
        <button
        onClick={onClick}
        disabled={disabled}
        aria-pressed={pressed}
        style={{
            ...sized,
            background: bg || "transparent",
            border: `1px solid ${shown}`,
            borderRadius: radius.sm,
            color: shown,
            fontFamily: type.family,
            fontWeight: type.weight.bold,
            cursor: disabled ? "not-allowed" : "pointer",
        }}
        >
        {label}
        </button>
    );
};
