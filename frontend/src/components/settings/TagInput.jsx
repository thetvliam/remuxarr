import { useState } from "react";
import { C } from "../../constants";

// Chip-based tag editor for string_list settings (language codes, paths, etc.)
//
// `normalize` prop: true (default) lowercases input — correct for language
// codes (eng, fre…). false preserves case exactly — required for filesystem
// paths, where SettingInput passes normalize={field.key !== "scan_paths"}.
export const TagInput = ({ values, onChange, normalize = true }) => {
    const [draft, setDraft] = useState("");

    const add = () => {
        // normalize=true for language codes (eng, fre…) — lowercase is correct.
        // normalize=false for filesystem paths — case must be preserved exactly.
        const v = normalize ? draft.trim().toLowerCase() : draft.trim();
        if (v && !values.includes(v)) {
            onChange([...values, v]);
            setDraft("");
        }
    };

    return (
        <div style={{ width: 220 }}>
        {/* Existing tags */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: 7 }}>
        {values.map(v => (
            <span
            key={v}
            style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                padding: "2px 7px",
                background: C.blue + "18",
                border: `1px solid ${C.blue}44`,
                color: C.blue,
                fontSize: 11,
            }}
            >
            {v}
            <button
            onClick={() => onChange(values.filter(x => x !== v))}
            style={{
                background: "none",
                border: "none",
                color: C.muted,
                cursor: "pointer",
                fontSize: 14,
                lineHeight: 1,
                padding: 0,
                fontFamily: "inherit",
            }}
            >
            ×
            </button>
            </span>
        ))}
        </div>

        {/* Add new tag */}
        <div style={{ display: "flex", gap: 4 }}>
        <input
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => e.key === "Enter" && add()}
        placeholder="e.g.  eng"
        style={{
            flex: 1,
            padding: "4px 8px",
            background: C.bg,
            border: `1px solid ${C.border}`,
            color: C.text,
            fontFamily: "inherit",
            fontSize: 11,
            outline: "none",
        }}
        />
        <button
        onClick={add}
        style={{
            padding: "4px 10px",
            background: C.border,
            border: "none",
            color: C.muted,
            fontFamily: "inherit",
            fontSize: 11,
            cursor: "pointer",
        }}
        >
        +
        </button>
        </div>
        </div>
    );
};
