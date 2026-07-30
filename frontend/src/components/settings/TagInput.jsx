import { useState } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";

// Chip-based tag editor for string_list settings (language codes, paths, etc.)
//
// `normalize` prop: true (default) lowercases input — correct for language
// codes (eng, fre…). false preserves case exactly — required for filesystem
// paths, where SettingInput passes normalize={field.key !== "scan_paths"}.
//
// `placeholder` prop: comes from the field's schema entry. It used to be
// hardcoded to a language-code example, so every list setting — scan paths,
// Plex path mappings, email recipients — prompted for "eng", which was
// actively misleading (the mappings field wants "/media/tv=/data/tv", a
// format nothing else hinted at). Fields with no schema placeholder fall
// back to a neutral prompt rather than an example from an unrelated setting.
export const TagInput = ({ values, onChange, normalize = true, placeholder = "" }) => {
    const { palette, type, space, legacy } = useTheme();
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
        <div style={{ display: "flex", flexWrap: "wrap", gap: space.xxs, marginBottom: legacy.subGapY }}>
        {values.map(v => (
            <span
            key={v}
            style={{
                display: "inline-flex",
                alignItems: "center",
                gap: space.xxs,
                padding: `${legacy.tagPadY}px ${legacy.tagPadX}px`,
                background: alpha(palette.blue, ALPHA.low),
                          border: `1px solid ${alpha(palette.blue, ALPHA.strong)}`,
                          color: palette.blue,
                          fontSize: type.size.md,
            }}
            >
            {v}
            <button
            onClick={() => onChange(values.filter(x => x !== v))}
            style={{
                background: "none",
                border: "none",
                color: palette.muted,
                cursor: "pointer",
                fontSize: type.size.xl,
                lineHeight: type.leading.none,
                padding: 0,
                fontFamily: type.family,
            }}
            >
            ×
            </button>
            </span>
        ))}
        </div>

        {/* Add new tag */}
        <div style={{ display: "flex", gap: space.xxs }}>
        <input
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => e.key === "Enter" && add()}
        placeholder={placeholder || "add an entry…"}
        style={{
            flex: 1,
            padding: `${space.xxs}px ${space.sm}px`,
            background: palette.bg,
            border: `1px solid ${palette.border}`,
            color: palette.text,
            fontFamily: type.family,
            fontSize: type.size.md,
            outline: "none",
        }}
        />
        <button
        onClick={add}
        style={{
            padding: `${space.xxs}px ${space.md}px`,
            background: palette.border,
            border: "none",
            color: palette.muted,
            fontFamily: type.family,
            fontSize: type.size.md,
            cursor: "pointer",
        }}
        >
        +
        </button>
        </div>
        </div>
    );
};
