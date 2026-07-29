import { palette, type, legacy } from "../../theme";

export const EmptyState = ({ msg }) => (
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
