import { useState } from "react";
import { C } from "../../constants";
import { Btn } from "../atoms/Btn";

/* ═══════════════════════════════════════════════════════════════════════════
 * API CONFIGURATOR  (small inline bar in the header)
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */
export const ApiBar = ({ current, onSave }) => {
    const [draft, setDraft] = useState(current);
    return (
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <input
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter") onSave(draft); if (e.key === "Escape") onSave(current); }}
        placeholder="http://localhost:9191"
        autoFocus
        style={{
            width: 210,
            padding: "3px 8px",
            background: C.bg,
            border: `1px solid ${C.border}`,
            color: C.text,
            fontFamily: "inherit",
            fontSize: 10,
            outline: "none",
        }}
        />
        <Btn label="SET" color={C.amber} onClick={() => onSave(draft)} />
        </div>
    );
};
