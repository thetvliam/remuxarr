import { useState } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { TagInput } from "./TagInput";

/* Kept separate so its draft state exists only for the control that needs it.
 *
 * The value is held as a raw string while the user is typing and only
 * clamped when they leave the field. Clamping on every keystroke made some
 * values impossible to type: for a field with min 2, clearing it and typing
 * "10" went "1" -> parsed 1 -> clamped to 2, so the field read "2" and the
 * next keystroke produced "20". 10 could not be reached at all.
 *
 * onChange still fires per keystroke with the parsed value, so the dirty
 * indicator and the Save button track what is on screen. The clamp on blur
 * is what guarantees the saved value is in range. */
const IntegerInput = ({ field, value, onChange }) => {
  const { palette, type, space, radius } = useTheme();
  const [draft, setDraft] = useState(null);   // non-null only while editing

  const clamp = (n) => {
    let out = n;
    if (field.min != null) out = Math.max(out, field.min);
    // field.max was declared in the schema and never applied.
    if (field.max != null) out = Math.min(out, field.max);
    return out;
  };

  return (
    <input
    type="number"
    min={field.min}
    max={field.max}
    value={draft ?? value ?? ""}
    onChange={(e) => {
      setDraft(e.target.value);
      const parsed = parseInt(e.target.value, 10);
      if (!Number.isNaN(parsed)) onChange(parsed);
    }}
    onBlur={() => {
      const parsed = parseInt(draft ?? "", 10);
      // Clearing the field, or leaving something non-numeric in it, used to
      // resolve to 0 regardless of what the setting means — for a field like
      // und_audio_threshold, 0 makes a ">=" comparison true for every file,
      // including ones with nothing wrong. Fall back to the declared floor.
      const fallback = field.min ?? 0;
      onChange(clamp(Number.isNaN(parsed) ? fallback : parsed));
      setDraft(null);
    }}
    style={{
      width: 72,
      padding: `${space.xs}px ${space.sm}px`,
      background: palette.bg,
      border: `1px solid ${palette.border}`,
      borderRadius: radius.sm,
      color: palette.text,
      fontFamily: type.family,
      fontSize: type.size.base,
    }}
    />
  );
};

// Renders the appropriate control for each setting type
export const SettingInput = ({ field, value, onChange }) => {
  const { palette, type, space, radius } = useTheme();
  if (field.type === "boolean") {
    const on = !!value;
    return (
      <button
      onClick={() => onChange(!on)}
      style={{
        padding: `${space.xs}px ${space.xl}px`,
        background: on ? alpha(palette.green, ALPHA.low) : "transparent",
        border: `1px solid ${on ? palette.green : palette.border}`,
        borderRadius: radius.sm,
        color: on ? palette.green : palette.dim,
        fontSize: type.size.sm,
        fontFamily: type.family,
        letterSpacing: type.tracking.wide,
        cursor: "pointer",
      }}
      >
      {on ? "■ ON" : "□ OFF"}
      </button>
    );
  }

  if (field.type === "integer") {
    return <IntegerInput field={field} value={value} onChange={onChange} />;
  }

  if (field.type === "string") {
    return (
      <input
      type={field.sensitive ? "password" : "text"}
      value={value ?? ""}
      onChange={e => onChange(e.target.value)}
      placeholder={field.placeholder || ""}
      style={{
        width: 220,
        padding: `${space.xs}px ${space.sm}px`,
        background: palette.bg,
        border: `1px solid ${palette.border}`,
        borderRadius: radius.sm,
        color: palette.text,
        fontFamily: type.family,
        fontSize: type.size.md,
      }}
      />
    );
  }

  if (field.type === "string_list") {
    return (
      <TagInput
      values={Array.isArray(value) ? value : []}
      onChange={onChange}
      placeholder={field.placeholder || ""}
      normalize={!["scan_paths", "plex_path_mappings"].includes(field.key)}
      />
    );
  }

  if (field.type === "select") {
    return (
      <select
      value={value ?? ""}
      onChange={e => onChange(e.target.value)}
      style={{
        width: 260,
        padding: `${space.xs}px ${space.sm}px`,
        background: palette.bg,
        border: `1px solid ${palette.border}`,
        borderRadius: radius.sm,
        color: palette.text,
        fontFamily: type.family,
        fontSize: type.size.md,
        cursor: "pointer",
      }}
      >
      {(field.options || []).map(opt => (
        <option key={opt.value} value={opt.value} style={{ background: palette.card }}>
        {opt.label}
        </option>
      ))}
      </select>
    );
  }

  return null;
};
