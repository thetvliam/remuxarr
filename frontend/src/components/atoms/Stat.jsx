import { useTheme } from "../../theme";

// Labelled stat cell
export const Stat = ({ label, value, color }) => {
    const { palette, type, space } = useTheme();
    return (
        <div>
        <div style={{
            color: palette.dim,
            fontSize: type.size.xs,
            letterSpacing: type.tracking.wider,
            marginBottom: space.xxs,
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
