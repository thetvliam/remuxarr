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
    aria-label={field.label || field.key}
    min={field.min}
    max={field.max}
    value={draft ?? value ?? ""}
    onChange={(e) => {
      setDraft(e.target.value);
      const parsed = parseInt(e.target.value, 10);
      if (!Number.isNaN(parsed)) onChange(parsed);
    }}
    onBlur={() => {
      // Nothing was typed, so there is nothing to commit. draft stays null
      // until the first keystroke, and parseInt(null ?? "") is NaN — which
      // fell through to the fallback below and REPLACED a perfectly good
      // stored value on an interaction as innocuous as tabbing past the
      // field. Every integer setting in the schema was affected:
      //
      //   und_audio_threshold      2   -> 1   (min)
      //   max_concurrent_jobs      1   -> 0
      //   job_timeout_minutes    120   -> 0
      //   email_smtp_port        587   -> 0
      //   email_failure_threshold  5   -> 0
      //
      // job_timeout_minutes is the worst of them: the worker reads
      // `float(timeout_minutes) * 60 if timeout_minutes else None`, and 0 is
      // falsy, so a hung FFmpeg job loses its timeout entirely. The field is
      // left dirty so the SaveBar does show a pending change, but nobody
      // associates that with a field they only tabbed through, and it rides
      // along with the next deliberate edit.
      if (draft === null) return;

      const parsed = parseInt(draft, 10);
      // Clearing the field, or leaving something non-numeric in it, used to
      // resolve to 0 regardless of what the setting means — for a field like
      // und_audio_threshold, 0 makes a ">=" comparison true for every file,
      // including ones with nothing wrong. Fall back to the declared floor.
      // This branch is now reachable only when the user actually emptied or
      // mistyped the field, which is what it was written for.
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
/* `field.label` is used as the accessible name on every control. FieldRow
 * renders the visible label as a separate <button> that toggles the
 * description, so there is no <label for> relationship to inherit and each
 * control was anonymous — a screen reader announced "edit text, blank"
 * for every setting on the page. */
export const SettingInput = ({ field, value, onChange }) => {
  const { palette, type, space, radius } = useTheme();
  if (field.type === "boolean") {
    const on = !!value;
    return (
      <button
      // A switch, not a button: role plus aria-checked is what conveys
      // on/off. It was announced as an unlabelled button whose only state
      // cue was the ■/□ glyph in its text.
      role="switch"
      aria-checked={on}
      aria-label={field.label || field.key}
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
      aria-label={field.label || field.key}
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
      label={field.label || field.key}
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
      aria-label={field.label || field.key}
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
