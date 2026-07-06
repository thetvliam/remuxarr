import { C } from "../../constants";
import { TagInput } from "./TagInput";

// Renders the appropriate control for each setting type
export const SettingInput = ({ field, value, onChange }) => {
  if (field.type === "boolean") {
    const on = !!value;
    return (
      <button
      onClick={() => onChange(!on)}
      style={{
        padding: "5px 14px",
        background: on ? C.green + "18" : "transparent",
        border: `1px solid ${on ? C.green : C.border}`,
        color: on ? C.green : C.dim,
        fontSize: 10,
        fontFamily: "inherit",
        letterSpacing: "0.1em",
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
        padding: "5px 8px",
        background: C.bg,
        border: `1px solid ${C.border}`,
        color: C.text,
        fontFamily: "inherit",
        fontSize: 12,
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
        padding: "5px 8px",
        background: C.bg,
        border: `1px solid ${C.border}`,
        color: C.text,
        fontFamily: "inherit",
        fontSize: 11,
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
        padding: "5px 8px",
        background: C.bg,
        border: `1px solid ${C.border}`,
        color: C.text,
        fontFamily: "inherit",
        fontSize: 11,
        outline: "none",
        cursor: "pointer",
      }}
      >
      {(field.options || []).map(opt => (
        <option key={opt.value} value={opt.value} style={{ background: C.card }}>
        {opt.label}
        </option>
      ))}
      </select>
    );
  }

  return null;
};
