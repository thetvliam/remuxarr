import { useTheme } from "../../theme";

// Labelled stat cell
export const Stat = ({ label, value, color }) => {
    const { palette, type, legacy } = useTheme();
    return (
        <div>
        <div style={{
            color: palette.dim,
            fontSize: type.size.xs,
            letterSpacing: type.tracking.wider,
            marginBottom: legacy.statGapY,
        }}>
        {label}
        </div>
        <div style={{
            color: color || palette.text,
            fontSize: type.size.base,
            fontWeight: type.weight.semibold,
        }}>
        {value ?? "—"}
        </div>
        </div>
    );
};
