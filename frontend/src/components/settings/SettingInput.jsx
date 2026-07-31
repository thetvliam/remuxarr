import { useTheme, alpha, ALPHA } from "../../theme";
import { TagInput } from "./TagInput";

// Renders the appropriate control for each setting type
export const SettingInput = ({ field, value, onChange }) => {
  const { palette, type, space } = useTheme();
  if (field.type === "boolean") {
    const on = !!value;
    return (
      <button
      onClick={() => onChange(!on)}
      style={{
        padding: `${space.xs}px ${space.xl}px`,
        background: on ? alpha(palette.green, ALPHA.low) : "transparent",
            border: `1px solid ${on ? palette.green : palette.border}`,
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
    return (
      <input
      type="number"
      min={field.min}
      value={value ?? ""}
      onChange={e => {
        const parsed = parseInt(e.target.value);
        // Clearing the field (or typing something non-numeric) used to
        // silently resolve to 0 regardless of what the setting actually
        // means — for a field like und_audio_threshold, 0 makes a
        // ">=" comparison true for every file, including ones with
        // nothing wrong. Fields with no declared min keep the exact
        // previous behavior (fall back to 0); fields that declare one
        // clamp both invalid input and in-range-but-too-low input up
        // to that floor.
        const fallback = field.min ?? 0;
        const next = Number.isNaN(parsed) ? fallback : parsed;
        onChange(field.min != null ? Math.max(next, field.min) : next);
      }}
      style={{
        width: 72,
        padding: `${space.xs}px ${space.sm}px`,
        background: palette.bg,
        border: `1px solid ${palette.border}`,
        color: palette.text,
        fontFamily: type.family,
        fontSize: type.size.base,
        outline: "none",
      }}
      />
    );
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
        color: palette.text,
        fontFamily: type.family,
        fontSize: type.size.md,
        outline: "none",
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
        color: palette.text,
        fontFamily: type.family,
        fontSize: type.size.md,
        outline: "none",
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
