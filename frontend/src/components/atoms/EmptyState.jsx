import { useTheme } from "../../theme";

export const EmptyState = ({ msg }) => {
    const { palette, type, legacy } = useTheme();
    return (
        <div style={{
            padding: `${legacy.emptyPadY}px ${legacy.emptyPadX}px`,
            textAlign: "center",
            color: palette.dim,
            fontSize: type.size.md,
            letterSpacing: type.tracking.snug,
        }}>
        {msg}
        </div>
    );
};
