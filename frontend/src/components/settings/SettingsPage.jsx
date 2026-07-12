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

/* ── Section header ─────────────────────────────────────────────────────── */
const SectionHeader = ({ label }) => (
  <div style={{
    display: "flex",
    alignItems: "center",
    gap: 10,
    margin: "32px 0 0",
    paddingBottom: 8,
    borderBottom: `1px solid ${C.border}`,
  }}>
    <span style={{
      color: C.amber,
      fontSize: 9,
      letterSpacing: "0.18em",
      fontWeight: 700,
    }}>
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
    // Auto-reset after 8 seconds
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
    <div style={{
      display: "flex",
      justifyContent: "flex-end",
      padding: "12px 0 4px",
    }}>
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
      display: "flex",
      alignItems: "center",
      gap: 8,
      padding: "10px 0 4px",
      color: C.muted,
      fontSize: 11,
    }}>
      <span style={{
        padding: "1px 7px",
        background: count > 0 ? C.amber + "18" : "transparent",
        border: `1px solid ${count > 0 ? C.amber + "55" : C.border}`,
        color: count > 0 ? C.amber : C.dim,
        fontSize: 10,
        fontWeight: 700,
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
      display: "flex",
      alignItems: "flex-start",
      gap: 8,
      padding: "10px 12px",
      marginTop: 8,
      background: C.red + "12",
      border: `1px solid ${C.red}55`,
      color: C.red,
      fontSize: 11,
      lineHeight: 1.6,
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

/* ═══════════════════════════════════════════════════════════════════════════
   SETTINGS PAGE
═══════════════════════════════════════════════════════════════════════════ */
export const SettingsPage = ({ api, toast, isMobile = false }) => {
  const [schema, setSchema] = useState([]);
  const [values, setValues] = useState({});
  const [status, setStatus] = useState("idle");

  useEffect(() => {
    Promise.all([
      fetch(`${api}/api/settings/schema`).then(r => r.json()),
      fetch(`${api}/api/settings`).then(r => r.json()),
    ])
      .then(([s, v]) => { setSchema(s); setValues(v); })
      .catch(() => {});
  }, [api]);

  const save = async () => {
    setStatus("saving");
    try {
      const schemaKeys   = new Set(schema.map(f => f.key));
      const schemaValues = Object.fromEntries(
        Object.entries(values).filter(([k]) => schemaKeys.has(k))
      );
      const r = await fetch(`${api}/api/settings`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(schemaValues),
      });
      setStatus(r.ok ? "saved" : "error");
      if (r.ok) setTimeout(() => setStatus("idle"), 2500);
    } catch (_) {
      setStatus("error");
    }
  };

  const set = (k, v) => setValues(prev => ({ ...prev, [k]: v }));

  // Group schema entries preserving declaration order
  const groups = Object.entries(
    schema.reduce((acc, field) => {
      const g = field.group || "General";
      if (!acc[g]) acc[g] = [];
      acc[g].push(field);
      return acc;
    }, {})
  );

  const btnColor = { idle: C.amber, saving: C.muted, saved: C.green, error: C.red }[status];

  return (
    <div style={{ maxWidth: 700, margin: "0 auto", padding: "28px 22px" }}>
      {/* Page header */}
      <div style={{ display: "flex", alignItems: "center", marginBottom: 12 }}>
        <span style={{ color: C.dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>
          CONFIGURATION
        </span>
        <button
          onClick={save}
          disabled={status === "saving"}
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
            cursor: status === "saving" ? "not-allowed" : "pointer",
          }}
        >
          {SAVE_LABEL[status]}
        </button>
      </div>

      {schema.length === 0 && (
        <div style={{ color: C.muted, fontSize: 11, textAlign: "center", padding: 32 }}>
          Connect to the backend to load settings…
        </div>
      )}

      {groups.map(([groupName, fields]) => (
        <div key={groupName}>
          <SectionHeader label={groupName} />
          {fields.map(field => (
            <FieldRow
              key={field.key}
              field={field}
              value={values[field.key]}
              onChange={v => set(field.key, v)}
              isMobile={isMobile}
            />
          ))}
          {(groupName === "Sonarr" || groupName === "Radarr" || groupName === "Plex" || groupName === "Email") && (
            <TestConnectionButton api={api} service={groupName.toLowerCase()} />
          )}
          {groupName === "Plex Analyze Backlog" && <PlexBacklogStatus api={api} />}
          {groupName === "Email" && <EmailBreakerStatus api={api} />}
        </div>
      ))}

      <MaintenanceSection api={api} toast={toast} />
      <LogViewer api={api} />
      <BackupRestoreSection api={api} toast={toast} />
      <FullBackupSection api={api} toast={toast} />
      <DangerZone api={api} toast={toast} />
    </div>
  );
};
