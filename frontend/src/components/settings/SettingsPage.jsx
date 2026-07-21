import { useState, useEffect } from "react";
import { C } from "../../constants";
import { fmtCount } from "../../utils";
import { SettingInput } from "./SettingInput";
import { DangerZone } from "./DangerZone";
import { BackupRestoreSection } from "./BackupRestoreSection";
import { FullBackupSection } from "./FullBackupSection";
import { MaintenanceSection } from "./MaintenanceSection";
import { LogViewer } from "./LogViewer";

const SAVE_LABEL = { idle: "SAVE CHANGES", saving: "SAVING…", saved: "✓ SAVED", error: "✗ ERROR" };

/* Category → which schema groups (or custom sections) live under it. The
   config categories list schema `group` names; the two action categories
   render their own components and have no saveable fields. */
const CATEGORIES = [
  { id: "processing",    label: "Library & Processing", groups: ["Library", "Metadata", "Audio", "Subtitles"] },
  { id: "worker",        label: "Worker",               groups: ["Worker"] },
  { id: "integrations",  label: "Integrations",         groups: ["Sonarr", "Radarr", "Plex", "Plex Analyze Backlog"] },
  { id: "notifications", label: "Notifications",        groups: ["Email"] },
  { id: "maintenance",   label: "Maintenance & Logs",   custom: "maintenance" },
  { id: "backup",        label: "Backup & Danger Zone", custom: "backup" },
];
const CATEGORY_IDS = new Set(CATEGORIES.map(c => c.id));
const STORAGE_KEY = "remuxarr.settingsCategory";

/* ── Section header ─────────────────────────────────────────────────────── */
const SectionHeader = ({ label, first }) => (
  <div style={{
    display: "flex",
    alignItems: "center",
    gap: 10,
    margin: first ? "4px 0 0" : "32px 0 0",
    paddingBottom: 8,
    borderBottom: `1px solid ${C.border}`,
  }}>
    <span style={{ color: C.amber, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>
      {label.toUpperCase()}
    </span>
  </div>
);

/* ── Test connection button ─────────────────────────────────────────────── */
const TestConnectionButton = ({ api, service }) => {
  const [state,  setState]  = useState("idle");   // idle | loading | ok | err
  const [result, setResult] = useState("");

  const run = async () => {
    setState("loading");
    setResult("");
    try {
      const r = await fetch(`${api}/api/settings/test-${service}`);
      const d = await r.json();
      if (d.success) {
        setState("ok");
        setResult(d.message || `${d.app || service} v${d.version}`);
      } else {
        setState("err");
        setResult(d.error || "Unknown error");
      }
    } catch (_) {
      setState("err");
      setResult("Request failed");
    }
    setTimeout(() => { setState("idle"); setResult(""); }, 8000);
  };

  const color = { idle: C.dim, loading: C.muted, ok: C.green, err: C.red }[state];
  const label = {
    idle:    "TEST CONNECTION",
    loading: "TESTING…",
    ok:      `✓ ${result}`,
    err:     `✗ ${result}`,
  }[state];

  return (
    <div style={{ display: "flex", justifyContent: "flex-end", padding: "12px 0 4px" }}>
      <button
        onClick={run}
        disabled={state === "loading"}
        style={{
          padding: "5px 14px",
          background: state === "idle" ? "transparent" : `${color}18`,
          border: `1px solid ${color}`,
          color,
          fontSize: 10,
          fontFamily: "inherit",
          fontWeight: 700,
          letterSpacing: "0.08em",
          cursor: state === "loading" ? "not-allowed" : "pointer",
          transition: "all 0.15s",
          maxWidth: 320,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </button>
    </div>
  );
};

/* ── Plex Analyze backlog status ────────────────────────────────────────── */
const PlexBacklogStatus = ({ api }) => {
  const [count, setCount] = useState(null);

  useEffect(() => {
    const poll = () => {
      fetch(`${api}/api/plex/backlog`)
        .then(r => r.json())
        .then(d => setCount(d.count ?? 0))
        .catch(() => {});
    };
    poll();
    const id = setInterval(poll, 10000);
    return () => clearInterval(id);
  }, [api]);

  if (count === null) return null;

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "10px 0 4px", color: C.muted, fontSize: 11,
    }}>
      <span style={{
        padding: "1px 7px",
        background: count > 0 ? C.amber + "18" : "transparent",
        border: `1px solid ${count > 0 ? C.amber + "55" : C.border}`,
        color: count > 0 ? C.amber : C.dim,
        fontSize: 10, fontWeight: 700,
      }}
        title={count >= 1000 ? count.toLocaleString() + " items" : undefined}
      >
        {fmtCount(count)}
      </span>
      <span>
        {count === 0
          ? "files queued for Plex re-analysis"
          : `${count === 1 ? "file" : "files"} queued for Plex re-analysis — drains during the configured window`
        }
      </span>
    </div>
  );
};

/* ── Email circuit-breaker status banner ──────────────────────────────────── */
const EmailBreakerStatus = ({ api }) => {
  const [state, setState] = useState(null);

  useEffect(() => {
    const poll = () => {
      fetch(`${api}/api/notifications/state`)
        .then(r => r.json())
        .then(setState)
        .catch(() => {});
    };
    poll();
    const id = setInterval(poll, 10000);
    return () => clearInterval(id);
  }, [api]);

  if (!state || !state.tripped) return null;

  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: 8,
      padding: "10px 12px", marginTop: 8,
      background: C.red + "12", border: `1px solid ${C.red}55`,
      color: C.red, fontSize: 11, lineHeight: 1.6,
    }}>
      <span style={{ flexShrink: 0 }}>⚠</span>
      <span>
        Failure notifications are paused after {state.consecutive_failures} consecutive
        job failures. No further emails will be sent until a job completes successfully —
        check the History panel's Failed tab to diagnose the issue.
      </span>
    </div>
  );
};

/* ── Settings field row ─────────────────────────────────────────────────── */
const FieldRow = ({ field, value, onChange, isMobile }) => (
  <div style={{
    display: "flex",
    flexDirection: isMobile ? "column" : "row",
    alignItems: "flex-start",
    gap: isMobile ? 10 : 24,
    padding: "16px 0",
    borderBottom: `1px solid ${C.border}`,
  }}>
    <div style={{ flex: 1 }}>
      <div style={{ color: C.text, fontSize: 12, fontWeight: 600, marginBottom: 5 }}>
        {field.label}
      </div>
      <div style={{ color: C.muted, fontSize: 11, lineHeight: 1.65 }}>
        {field.description}
      </div>
    </div>
    <div style={{ flexShrink: 0, paddingTop: 2 }}>
      <SettingInput field={field} value={value} onChange={onChange} />
    </div>
  </div>
);

/* ── Sidebar / dropdown navigation ──────────────────────────────────────── */
const NavSidebar = ({ active, onSelect, dirty }) => (
  <nav style={{
    flexShrink: 0,
    width: 190,
    position: "sticky",
    top: 0,
    alignSelf: "flex-start",
    display: "flex",
    flexDirection: "column",
    gap: 2,
    paddingRight: 18,
    borderRight: `1px solid ${C.border}`,
  }}>
    {CATEGORIES.map(c => {
      const on = c.id === active;
      return (
        <button
          key={c.id}
          onClick={() => onSelect(c.id)}
          style={{
            textAlign: "left",
            padding: "9px 12px",
            background: on ? C.amber + "14" : "transparent",
            border: "none",
            borderLeft: `2px solid ${on ? C.amber : "transparent"}`,
            color: on ? C.amber : C.muted,
            fontSize: 11,
            fontFamily: "inherit",
            fontWeight: on ? 700 : 500,
            letterSpacing: "0.03em",
            cursor: "pointer",
            transition: "all 0.12s",
          }}
        >
          {c.label}
        </button>
      );
    })}
    {dirty && (
      <div style={{ marginTop: 14, paddingLeft: 12, color: C.amber, fontSize: 9, letterSpacing: "0.1em", fontWeight: 700 }}>
        ● UNSAVED
      </div>
    )}
  </nav>
);

const NavDropdown = ({ active, onSelect }) => (
  <select
    value={active}
    onChange={e => onSelect(e.target.value)}
    style={{
      flex: 1,
      minWidth: 0,
      padding: "9px 10px",
      background: C.card,
      border: `1px solid ${C.border}`,
      color: C.text,
      fontSize: 12,
      fontFamily: "inherit",
      fontWeight: 600,
      cursor: "pointer",
    }}
  >
    {CATEGORIES.map(c => (
      <option key={c.id} value={c.id} style={{ background: C.card, color: C.text }}>
        {c.label}
      </option>
    ))}
  </select>
);

/* ── Persistent save bar (status + button; caller wraps it sticky) ──────── */
const SaveBar = ({ status, dirty, dirtyCount, onSave }) => {
  const btnColor = dirty
    ? { idle: C.amber, saving: C.muted, saved: C.green, error: C.red }[status]
    : C.dim;
  const statusText = status === "saving" ? "Saving…"
    : status === "error" ? "Save failed — check the connection"
    : status === "saved" ? "Changes saved"
    : dirty ? `${dirtyCount} unsaved change${dirtyCount === 1 ? "" : "s"}`
    : "All changes saved";
  const statusColor = status === "error" ? C.red
    : status === "saved" ? C.green
    : dirty ? C.amber : C.muted;

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 12,
      padding: "12px 0",
      background: C.bg,
      borderBottom: `1px solid ${C.border}`,
    }}>
      <span style={{ color: statusColor, fontSize: 10, letterSpacing: "0.12em", fontWeight: 700 }}>
        {dirty && status === "idle" ? "● " : ""}{statusText.toUpperCase()}
      </span>
      <button
        onClick={onSave}
        disabled={status === "saving" || !dirty}
        style={{
          marginLeft: "auto",
          padding: "6px 18px",
          background: btnColor + "22",
          border: `1px solid ${btnColor}`,
          color: btnColor,
          fontSize: 10,
          fontFamily: "inherit",
          fontWeight: 700,
          letterSpacing: "0.1em",
          cursor: (status === "saving" || !dirty) ? "default" : "pointer",
          transition: "all 0.15s",
        }}
      >
        {SAVE_LABEL[status]}
      </button>
    </div>
  );
};

/* ═══════════════════════════════════════════════════════════════════════════
   SETTINGS PAGE
═══════════════════════════════════════════════════════════════════════════ */
export const SettingsPage = ({ api, toast, isMobile = false, onDirtyChange }) => {
  const [schema,   setSchema]   = useState([]);
  const [values,   setValues]   = useState({});
  const [baseline, setBaseline] = useState({});   // last-saved snapshot (dirty is measured against this)
  const [status,   setStatus]   = useState("idle");
  const [active,   setActive]   = useState(() => {
    try {
      const s = localStorage.getItem(STORAGE_KEY);
      return s && CATEGORY_IDS.has(s) ? s : CATEGORIES[0].id;
    } catch (_) { return CATEGORIES[0].id; }
  });

  useEffect(() => {
    Promise.all([
      fetch(`${api}/api/settings/schema`).then(r => r.json()),
      fetch(`${api}/api/settings`).then(r => r.json()),
    ])
      .then(([s, v]) => { setSchema(s); setValues(v); setBaseline(v); })
      .catch(() => {});
  }, [api]);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, active); } catch (_) { /* ignore */ }
  }, [active]);

  // Dirty = any schema field whose current value differs from the saved snapshot.
  const dirtyKeys = schema
    .map(f => f.key)
    .filter(k => JSON.stringify(values[k]) !== JSON.stringify(baseline[k]));
  const isDirty = dirtyKeys.length > 0;

  // Report dirtiness up so the app can guard navigation, and reset on unmount.
  useEffect(() => { onDirtyChange?.(isDirty); }, [isDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  // Warn on browser refresh / tab close while there are unsaved edits.
  useEffect(() => {
    if (!isDirty) return;
    const h = (e) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [isDirty]);

  const save = async () => {
    const snapshot = values;
    setStatus("saving");
    try {
      const schemaKeys   = new Set(schema.map(f => f.key));
      const schemaValues = Object.fromEntries(
        Object.entries(snapshot).filter(([k]) => schemaKeys.has(k))
      );
      const r = await fetch(`${api}/api/settings`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(schemaValues),
      });
      if (r.ok) {
        setBaseline(snapshot);          // saved values become the new clean baseline
        setStatus("saved");
        setTimeout(() => setStatus("idle"), 2500);
      } else {
        setStatus("error");
      }
    } catch (_) {
      setStatus("error");
    }
  };

  const set = (k, v) => setValues(prev => ({ ...prev, [k]: v }));

  // schema grouped by declared group name
  const groupsMap = schema.reduce((acc, field) => {
    const g = field.group || "General";
    (acc[g] = acc[g] || []).push(field);
    return acc;
  }, {});

  const renderGroup = (groupName, first) => {
    const fields = groupsMap[groupName] || [];
    if (fields.length === 0) return null;
    return (
      <div key={groupName}>
        <SectionHeader label={groupName} first={first} />
        {fields.map(field => (
          <FieldRow
            key={field.key}
            field={field}
            value={values[field.key]}
            onChange={v => set(field.key, v)}
            isMobile={isMobile}
          />
        ))}
        {["Sonarr", "Radarr", "Plex", "Email"].includes(groupName) && (
          <TestConnectionButton api={api} service={groupName.toLowerCase()} />
        )}
        {groupName === "Plex Analyze Backlog" && <PlexBacklogStatus api={api} />}
        {groupName === "Email" && <EmailBreakerStatus api={api} />}
      </div>
    );
  };

  const cat = CATEGORIES.find(c => c.id === active) || CATEGORIES[0];

  const renderCategory = () => {
    if (cat.custom === "maintenance") {
      return (
        <>
          <MaintenanceSection api={api} toast={toast} />
          <LogViewer api={api} />
        </>
      );
    }
    if (cat.custom === "backup") {
      return (
        <>
          <BackupRestoreSection api={api} toast={toast} />
          <FullBackupSection api={api} toast={toast} />
          <DangerZone api={api} toast={toast} />
        </>
      );
    }
    if (schema.length === 0) {
      return (
        <div style={{ color: C.muted, fontSize: 11, textAlign: "center", padding: 32 }}>
          Connect to the backend to load settings…
        </div>
      );
    }
    return cat.groups.map((g, i) => renderGroup(g, i === 0));
  };

  const saveBar = (
    <SaveBar status={status} dirty={isDirty} dirtyCount={dirtyKeys.length} onSave={save} />
  );

  // ── Mobile: sticky dropdown + save bar stacked above the content ──────────
  if (isMobile) {
    return (
      <div style={{ maxWidth: 700, margin: "0 auto", padding: "16px 16px 40px" }}>
        <div style={{ position: "sticky", top: 0, zIndex: 6, background: C.bg }}>
          <div style={{ padding: "2px 0 8px" }}>
            <NavDropdown active={active} onSelect={setActive} />
          </div>
          {saveBar}
        </div>
        <div style={{ marginTop: 6 }}>{renderCategory()}</div>
      </div>
    );
  }

  // ── Desktop: sticky sidebar + content with a sticky save bar ──────────────
  return (
    <div style={{ maxWidth: 940, margin: "0 auto", padding: "24px 22px 48px", display: "flex", gap: 26 }}>
      <NavSidebar active={active} onSelect={setActive} dirty={isDirty} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ position: "sticky", top: 0, zIndex: 5 }}>
          {saveBar}
        </div>
        <div style={{ marginTop: 6 }}>{renderCategory()}</div>
      </div>
    </div>
  );
};
