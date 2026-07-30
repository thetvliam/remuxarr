import { useTheme } from "../../theme";

export const EmptyState = ({ msg }) => {
    const { palette, type, space } = useTheme();
    return (
        <div style={{
            padding: `${space.giant}px ${space.xl}px`,
            textAlign: "center",
            color: palette.dim,
            fontSize: type.size.md,
            letterSpacing: type.tracking.snug,
        }}>
        {msg}
        </div>
    );
};
