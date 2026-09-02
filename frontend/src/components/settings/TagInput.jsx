import { useState } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";

// Chip-based tag editor for string_list settings (language codes, paths, etc.)
//
// `normalize` prop: true (default) lowercases input — correct for language
// codes (eng, fre…). false preserves case exactly — required for filesystem
// paths, where SettingInput passes
// normalize={!["scan_paths", "plex_path_mappings"].includes(field.key)}.
// The list matters: it previously named scan_paths alone, and lowercasing
// a Plex path mapping breaks it on a case-sensitive filesystem.
//
// `placeholder` prop: comes from the field's schema entry. It used to be
// hardcoded to a language-code example, so every list setting — scan paths,
// Plex path mappings, email recipients — prompted for "eng", which was
// actively misleading (the mappings field wants "/media/tv=/data/tv", a
// format nothing else hinted at). Fields with no schema placeholder fall
// back to a neutral prompt rather than an example from an unrelated setting.
export const TagInput = ({ values, onChange, normalize = true, placeholder = "", label }) => {
    const { palette, type, space, radius } = useTheme();
    const [draft, setDraft] = useState("");
    const [error, setError] = useState("");

    const add = () => {
        // normalize=true for language codes (eng, fre…) — lowercase is correct.
        // normalize=false for filesystem paths — case must be preserved exactly.
        const v = normalize ? draft.trim().toLowerCase() : draft.trim();
        if (!v) return;
        // Reported rather than ignored. Re-adding an existing entry did
        // nothing at all and did not even clear the box, so the entry sat
        // there looking unsubmitted — identical to a keypress that had not
        // registered, and there is no way to tell from the chips above
        // whether "ENG" and "eng" are the same one when normalize is on.
        // TimeTagInput, the same control for schedule times, already says so.
        if (values.includes(v)) {
            setError(`"${v}" is already in the list`);
            return;
        }
        onChange([...values, v]);
        setDraft("");
        setError("");
    };

    return (
        <div style={{ width: 220 }}>
        {/* Existing tags */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: space.xxs, marginBottom: space.sm }}>
        {values.map(v => (
            <span
            key={v}
            style={{
                display: "inline-flex",
                alignItems: "center",
                gap: space.xxs,
                padding: `${space.hair}px ${space.sm}px`,
                background: alpha(palette.blue, ALPHA.low),
                          border: `1px solid ${alpha(palette.blue, ALPHA.strong)}`,
                          borderRadius: radius.sm,
                          color: palette.blue,
                          fontSize: type.size.md,
            }}
            >
            {v}
            <button
            // TimeTagInput's × is labelled; these were not. Same control,
            // same glyph, so it should read the same way.
            aria-label={`Remove ${v}`}
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
        aria-label={label ? `Add to ${label}` : "Add"}
        value={draft}
        onChange={e => { setDraft(e.target.value); setError(""); }}
        /* preventDefault so Enter cannot submit an enclosing form, and comma
         *         as a second separator: both match TimeTagInput, and typing a list
         *         of language codes with commas is the obvious thing to try. */
        onKeyDown={e => {
            if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); }
            if (e.key === "Escape") { setDraft(""); setError(""); }
        }}
        placeholder={placeholder || "add an entry…"}
        style={{
            flex: 1,
            padding: `${space.xxs}px ${space.sm}px`,
            background: palette.bg,
            border: `1px solid ${palette.border}`,
            borderRadius: radius.sm,
            color: palette.text,
            fontFamily: type.family,
            fontSize: type.size.md,
        }}
        />
        <button
        aria-label="Add"
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
        {error && (
            <div role="alert" style={{ color: palette.red, fontSize: type.size.sm, marginTop: space.xs }}>
            {error}
            </div>
        )}
        </div>
    );
};
