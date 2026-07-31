import { useState } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { Btn } from "../atoms/Btn";

/* ═══════════════════════════════════════════════════════════════════════════
 * API CONFIGURATOR  (small inline bar in the header)
 ═ * * * ═*═════════════════════════════════════════════════════════════════════════ */
export const ApiBar = ({ current, onSave }) => {
    const { palette, type, space, legacy } = useTheme();
    const [draft, setDraft] = useState(current);
    return (
        <div style={{ display: "flex", alignItems: "center", gap: space.xs }}>
        <input
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter") onSave(draft); if (e.key === "Escape") onSave(current); }}
        placeholder="http://localhost:9191"
        autoFocus
        style={{
            width: legacy.apiBarW,
            padding: `${space.xxs}px ${space.sm}px`,
            background: palette.bg,
            border: `1px solid ${palette.border}`,
            color: palette.text,
            fontFamily: type.family,
            fontSize: type.size.sm,
            outline: "none",
        }}
        />
        <Btn label="SET" color={palette.amber} onClick={() => onSave(draft)} />
        </div>
    );
};
