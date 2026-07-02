import { C } from "../../constants";

export const EmptyState = ({ msg }) => (
    <div style={{
        padding: "38px 16px",
        textAlign: "center",
        color: C.dim,
        fontSize: 11,
        letterSpacing: "0.06em",
    }}>
    {msg}
    </div>
);
