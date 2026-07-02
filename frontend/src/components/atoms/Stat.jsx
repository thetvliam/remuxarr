import { C } from "../../constants";

// Labelled stat cell
export const Stat = ({ label, value, color }) => (
    <div>
    <div style={{ color: C.dim, fontSize: 9, letterSpacing: "0.12em", marginBottom: 3 }}>
    {label}
    </div>
    <div style={{ color: color || C.text, fontSize: 12, fontWeight: 600 }}>
    {value ?? "—"}
    </div>
    </div>
);
