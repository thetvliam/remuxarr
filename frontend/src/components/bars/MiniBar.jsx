import { C } from "../../constants";

/* ═══════════════════════════════════════════════════════════════════════════
 * MINI PROGRESS BAR  (used inside queue rows while processing)
 ═ ═*═════════════════════════════════════════════════════════════════════════ */
export const MiniBar = ({ value = 0, segments = 28 }) => (
    <div style={{ display: "flex", gap: 1 }}>
    {Array.from({ length: segments }, (_, i) => (
        <div
        key={i}
        style={{
            flex: 1,
            height: 3,
            background: i < Math.round((value / 100) * segments) ? C.blue : C.border,
                                                 transition: "background 0.06s",
        }}
        />
    ))}
    </div>
);
