import { useState, useEffect, useCallback } from "react";
import { useTheme, alpha, ALPHA, LAYER } from "../../theme";
import { fmtCount } from "../../utils";
import { SettingInput } from "./SettingInput";
import { DangerZone } from "./DangerZone";
import { AppearanceSection } from "./AppearanceSection";
import { BackupRestoreSection } from "./BackupRestoreSection";
import { FullBackupSection } from "./FullBackupSection";
import { MaintenanceSection } from "./MaintenanceSection";
import { LogViewer } from "./LogViewer";

const SAVE_LABEL = { idle: "SAVE CHANGES", saving: "SAVING…", saved: "✓ SAVED", error: "✗ ERROR" };

/* Category → which schema groups (or custom sections) live under it. The
 * config categories list schema `group` names; the two action categories
 * render their own components and have no saveable fields. */
const CATEGORIES = [
  { id: "processing",    label: "Library & Processing", groups: ["Library", "Metadata", "Audio", "Subtitles"] },
{ id: "worker",        label: "Worker",               groups: ["Worker"] },
{ id: "recyclebin",    label: "Recycle Bin",          groups: ["Recycle Bin"] },
{ id: "integrations",  label: "Integrations",         groups: ["Sonarr", "Radarr", "Plex", "Plex Analyze Backlog"] },
{ id: "notifications", label: "Notifications",        groups: ["Email"] },
{ id: "maintenance",   label: "Maintenance & Logs",   custom: "maintenance" },
{ id: "backup",        label: "Backup & Danger Zone", custom: "backup" },
{ id: "appearance",    label: "Appearance",           custom: "appearance" },
];
const CATEGORY_IDS = new Set(CATEGORIES.map(c => c.id));
const STORAGE_KEY = "remuxarr.settingsCategory";

/* ── Section header ─────────────────────────────────────────────────────── */
const SectionHeader = ({ label, first }) => {
  const { palette, type, space } = useTheme();
  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: space.md,
      margin: first ? `${space.xxs}px 0 0` : `${space.xxxl}px 0 0`,
      paddingBottom: space.sm,
      borderBottom: `1px solid ${palette.border}`,
    }}>
    <span style={{ color: palette.amber, fontSize: type.size.xs, letterSpacing: type.tracking.max, fontWeight: type.weight.bold }}>
    {label.toUpperCase()}
    </span>
    </div>
  );
};

/* ── Test connection button ─────────────────────────────────────────────── */
const TestConnectionButton = ({ api, service }) => {
  const { palette, type, space, radius } = useTheme();
  const [state,  setState]  = useState("idle");   // idle | loading | ok | err
  const [result, setResult] = useState("");

  // Clear the result after 8s, in an effect with cleanup rather than a bare
  // setTimeout in the handler. Two tests in quick succession previously left
  // two timers running, so the first could wipe the second's result early —
  // and either could fire after unmount, setting state on a dead component.
  useEffect(() => {
    if (state !== "ok" && state !== "err") return;
    const t = setTimeout(() => { setState("idle"); setResult(""); }, 8000);
    return () => clearTimeout(t);
  }, [state]);

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
    } catch (err) {
      console.error("Connection test request failed", err);
      setState("err");
      setResult("Request failed");
    }
  };

  const color = { idle: palette.dim, loading: palette.muted, ok: palette.green, err: palette.red }[state];
  const label = {
    idle:    "TEST CONNECTION",
    loading: "TESTING…",
    ok:      `✓ ${result}`,
    err:     `✗ ${result}`,
  }[state];

  return (
    <div style={{ display: "flex", justifyContent: "flex-end", padding: `${space.lg}px 0 ${space.xxs}px` }}>
    <button
    onClick={run}
    disabled={state === "loading"}
    style={{
      padding: `${space.xs}px ${space.xl}px`,
      background: state === "idle" ? "transparent" : `${alpha(color, ALPHA.low)}`,
          border: `1px solid ${color}`,
          borderRadius: radius.sm,
          color,
          fontSize: type.size.sm,
          fontFamily: type.family,
          fontWeight: type.weight.bold,
          letterSpacing: type.tracking.normal,
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
  const { palette, type, space, radius } = useTheme();
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
      display: "flex", alignItems: "center", gap: space.sm,
      padding: `${space.md}px 0 ${space.xxs}px`, color: palette.muted, fontSize: type.size.md,
    }}>
    <span style={{
      padding: `${space.hair}px ${space.sm}px`,
      background: count > 0 ? alpha(palette.amber, ALPHA.low) : "transparent",
          border: `1px solid ${count > 0 ? alpha(palette.amber, ALPHA.heavy) : palette.border}`,
          borderRadius: radius.sm,
          color: count > 0 ? palette.amber : palette.dim,
          fontSize: type.size.sm, fontWeight: type.weight.bold,
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
  const { palette, type, space, radius } = useTheme();
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

  if (!state?.tripped) return null;

  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: space.sm,
      padding: `${space.md}px ${space.lg}px`, marginTop: space.sm,
      background: alpha(palette.red, ALPHA.trace), border: `1px solid ${alpha(palette.red, ALPHA.heavy)}`,
          borderRadius: radius.sm,
          color: palette.red, fontSize: type.size.md, lineHeight: type.leading.normal,
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

/* ── Settings field row ─────────────────────────────────────────────────────
 * The description is collapsed by default and revealed by clicking the
 * setting's name — hovering the name surfaces a "?" to advertise that it's
 * clickable. Descriptions vary from a few words to several lines, and
 * showing them all at once was most of the page's height. The "?" slot is
 * always laid out (just transparent until hover) so nothing shifts as the
 * pointer moves down the list, and it stays visible on mobile, where there
 * is no hover to discover it with. */
const FieldRow = ({ field, value, onChange, isMobile, immediate = false }) => {
  const { palette, type, space, radius } = useTheme();
  const [open,  setOpen]  = useState(false);
  const [hover, setHover] = useState(false);

  const hasDesc  = !!(field.description || "").trim();
  const active   = open || hover;
  const showHint = hasDesc && (active || isMobile);
  const hintColor = active ? palette.amber : palette.dim;

  return (
    <div style={{ padding: `${space.lg}px 0`, borderBottom: `1px solid ${palette.border}` }}>
    <div style={{
      display: "flex",
      flexDirection: isMobile ? "column" : "row",
      alignItems: "flex-start",
      gap: isMobile ? space.md : space.huge,
    }}>
    <div style={{ flex: 1, minWidth: 0 }}>
    <button
    type="button"
    onClick={() => hasDesc && setOpen(o => !o)}
    onMouseEnter={() => setHover(true)}
    onMouseLeave={() => setHover(false)}
    aria-expanded={hasDesc ? open : undefined}
    title={hasDesc ? (open ? "Hide description" : "Show description") : undefined}
    style={{
      display: "inline-flex",
      alignItems: "center",
      gap: space.xs,
      padding: 0,
      background: "none",
      border: "none",
      textAlign: "left",
      color: active && hasDesc ? palette.amber : palette.text,
      fontSize: type.size.base,
      fontFamily: type.family,
      fontWeight: type.weight.semibold,
      cursor: hasDesc ? "pointer" : "default",
      transition: "color 0.12s",
    }}
    >
    {field.label}
    {hasDesc && (
      <span
      aria-hidden="true"
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: 13,
        height: 13,
        flexShrink: 0,
        borderRadius: radius.full,
        border: `1px solid ${showHint ? hintColor : "transparent"}`,
        color: showHint ? hintColor : "transparent",
        fontSize: type.size.xs,
        fontWeight: type.weight.bold,
        lineHeight: type.leading.none,
        transition: "color 0.12s, border-color 0.12s",
      }}
      >
      ?
      </span>
    )}
    </button>

    {open && hasDesc && (
      <div style={{
        color: palette.muted,
        fontSize: type.size.md,
        lineHeight: type.leading.relaxed,
        marginTop: space.sm,
        paddingRight: isMobile ? 0 : 14,
      }}>
      {field.description}
      </div>
    )}

    {/* Without this the row is indistinguishable from its neighbours while
      behaving differently — it commits on click and never appears in the
      SaveBar count. An unexplained inconsistency reads as a bug. */}
      {immediate && (
        <div style={{
          color: palette.dim,
          fontSize: type.size.xs,
          letterSpacing: type.tracking.snug,
          marginTop: space.xs,
        }}>
        Applies immediately — no save needed
        </div>
      )}
      </div>

      <div style={{ flexShrink: 0, paddingTop: space.hair }}>
      <SettingInput field={field} value={value} onChange={onChange} />
      </div>
      </div>
      </div>
  );
};

/* ── Sidebar / dropdown navigation ──────────────────────────────────────── */
const NavSidebar = ({ active, onSelect, dirty }) => {
  const { palette, type, space, size } = useTheme();
  return (
    <nav style={{
      flexShrink: 0,
      width: 190,
      position: "sticky",
      top: 0,
      alignSelf: "flex-start",
      display: "flex",
      flexDirection: "column",
      gap: space.hair,
      paddingRight: space.xxl,
      borderRight: `1px solid ${palette.border}`,
    }}>
    {CATEGORIES.map(c => {
      const on = c.id === active;
      return (
        <button
        key={c.id}
        onClick={() => onSelect(c.id)}
        style={{
          textAlign: "left",
          padding: `${space.md}px ${space.lg}px`,
          background: on ? alpha(palette.amber, ALPHA.soft) : "transparent",
              border: "none",
              borderLeft: `${size.accentThin}px solid ${on ? palette.amber : "transparent"}`,
              color: on ? palette.amber : palette.muted,
              fontSize: type.size.md,
              fontFamily: type.family,
              fontWeight: on ? type.weight.bold : type.weight.medium,
              letterSpacing: type.tracking.tight,
              cursor: "pointer",
              transition: "all 0.12s",
        }}
        >
        {c.label}
        </button>
      );
    })}
    {dirty && (
      <div style={{ marginTop: space.xl, paddingLeft: space.lg, color: palette.amber, fontSize: type.size.xs, letterSpacing: type.tracking.wide, fontWeight: type.weight.bold }}>
      ● UNSAVED
      </div>
    )}
    </nav>
  );
};

const NavDropdown = ({ active, onSelect }) => {
  const { palette, type, space, radius } = useTheme();
  return (
    <select
    value={active}
    onChange={e => onSelect(e.target.value)}
    style={{
      flex: 1,
      minWidth: 0,
      padding: `${space.md}px ${space.md}px`,
      background: palette.card,
      border: `1px solid ${palette.border}`,
      borderRadius: radius.sm,
      color: palette.text,
      fontSize: type.size.base,
      fontFamily: type.family,
      fontWeight: type.weight.semibold,
      cursor: "pointer",
    }}
    >
    {CATEGORIES.map(c => (
      <option key={c.id} value={c.id} style={{ background: palette.card, color: palette.text }}>
      {c.label}
      </option>
    ))}
    </select>
  );
};

/* ── Persistent save bar (status + button; caller wraps it sticky) ──────── */
const SaveBar = ({ status, dirty, dirtyCount, onSave }) => {
  const { palette, type, space, radius } = useTheme();
  const btnColor = dirty
  ? { idle: palette.amber, saving: palette.muted, saved: palette.green, error: palette.red }[status]
  : palette.dim;
  /* `dirty` outranks the "saved" confirmation. save() captures the values it
   * is going to send before awaiting, and anything typed while the PUT is in
   * flight is correctly left dirty afterwards — it was not part of the
   * request. But the confirmation fired regardless, so for the 2.5s it
   * lasted the bar read CHANGES SAVED in green with an unsaved edit sitting
   * on screen and the Save button still enabled beside it. The edit is not
   * lost; the label just contradicted it. */
  const statusText = status === "saving" ? "Saving…"
  : status === "error" ? "Save failed — check the connection"
  : dirty ? `${dirtyCount} unsaved change${dirtyCount === 1 ? "" : "s"}`
  : status === "saved" ? "Changes saved"
  : "All changes saved";
  const statusColor = status === "error" ? palette.red
  : dirty ? palette.amber
  : status === "saved" ? palette.green : palette.muted;

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: space.lg,
      padding: `${space.lg}px 0`,
      background: palette.bg,
      borderBottom: `1px solid ${palette.border}`,
    }}>
    <span style={{ color: statusColor, fontSize: type.size.sm, letterSpacing: type.tracking.wider, fontWeight: type.weight.bold }}>
    {dirty && status !== "saving" ? "● " : ""}{statusText.toUpperCase()}
    </span>
    <button
    onClick={onSave}
    disabled={status === "saving" || !dirty}
    style={{
      marginLeft: "auto",
      padding: `${space.xs}px ${space.xxl}px`,
      background: alpha(btnColor, ALPHA.medium),
          border: `1px solid ${btnColor}`,
          borderRadius: radius.sm,
          color: btnColor,
          fontSize: type.size.sm,
          fontFamily: type.family,
          fontWeight: type.weight.bold,
          letterSpacing: type.tracking.wide,
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
 * SETTINGS PAGE
 ═ * ═*═════════════════════════════════════════════════════════════════════════ */
export const SettingsPage = ({ api, toast, isMobile = false, onDirtyChange, liveToggles = {} }) => {
  const { palette, type, space } = useTheme();
  const [schema,   setSchema]   = useState([]);
  const [values,   setValues]   = useState({});
  const [baseline, setBaseline] = useState({});   // last-saved snapshot (dirty is measured against this)
  const [status,   setStatus]   = useState("idle");
  const [loadError, setLoadError] = useState(false);

  // Drop the "saved" confirmation back to idle after 2.5s. Same reason as
  // above: the bare setTimeout it replaces could fire into a remounted
  // Settings page and clear a confirmation the user had just triggered.
  useEffect(() => {
    if (status !== "saved") return;
    const t = setTimeout(() => setStatus("idle"), 2500);
    return () => clearTimeout(t);
  }, [status]);
  const [active,   setActive]   = useState(() => {
    try {
      const s = localStorage.getItem(STORAGE_KEY);
      return s && CATEGORY_IDS.has(s) ? s : CATEGORIES[0].id;
    } catch (_) { return CATEGORIES[0].id; }
  });

  // Extracted from the mount effect so it can be re-run on demand. Anything
  // that changes settings server-side WITHOUT going through this page's own
  // save() has to call this, or the page keeps rendering pre-change values —
  // see the reload prop passed to BackupRestoreSection below.
  const loadSettings = useCallback(() => {
    return Promise.all([
      fetch(`${api}/api/settings/schema`).then(r => r.json()),
                       fetch(`${api}/api/settings/`).then(r => r.json()),
    ])
    .then(([s, v]) => {
      setSchema(s);
      setValues(v);
      // baseline must move with values. It is what isDirty compares against,
      // so leaving it stale would leave the page looking clean while showing
      // different data than it holds.
      setBaseline(v);
      setLoadError(false);
    })
    .catch((err) => {
      // Not silent: this is a one-shot load, and on failure `schema` stays []
      // so the entire settings page renders as an empty shell with no field,
      // no error and nothing to retry — indistinguishable from "this app has
      // no settings". The pollers above swallow deliberately (they retry every
      // 10s and would flood the console); this one gets exactly one report.
      console.error("Failed to load settings schema/values", err);
      setLoadError(true);
    });
  }, [api]);

  useEffect(() => { loadSettings(); }, [loadSettings]);

  // Bumped alongside loadSettings so sibling sections that fetch their own
  // settings independently (MaintenanceSection reads three keys directly) also
  // refetch. Without it the main fields would update after an import while
  // those three toggles kept showing pre-import state.
  const [reloadKey, setReloadKey] = useState(0);

  const reloadAllSettings = useCallback(() => {
    setReloadKey(k => k + 1);
    return loadSettings();
  }, [loadSettings]);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, active); } catch (_) { /* ignore */ }
  }, [active]);

  // Dirty = any schema field whose current value differs from the saved snapshot.
  //
  // Live-toggle keys are excluded by definition rather than by accident. Their
  // onChange goes to the shared action instead of set(), so `values` never
  // moves for them and they would not appear here anyway — but relying on that
  // makes a real invariant look incidental. Being explicit also keeps the
  // SaveBar count honest: toggling dry run must not make the page claim an
  // unsaved change, since the change is already saved.
  const dirtyKeys = schema
  .map(f => f.key)
  .filter(k => !liveToggles[k])
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
      // Only the keys the user actually changed on this page.
      //
      // This previously sent EVERY schema key from `values`, which is a
      // snapshot taken at page load. Two schema fields — dry_run_mode and
      // auto_start_jobs, both group "Worker" — are also written from outside
      // this page: the header toggles both, and abort_job sets
      // auto_start_jobs=False server-side as a safety stop. So:
      //
      //   1. open Settings
      //   2. toggle DRY RUN in the header (backend now true, toast confirms)
      //   3. change anything unrelated, press SAVE
      //   4. the PUT carries dry_run_mode:false from step 1's snapshot
      //
      // Dry run silently switches off, and the same shape re-enables
      // auto_start_jobs after an abort — undoing the stop the abort button
      // exists to apply. BackupRestoreSection's comment already diagnosed
      // this exact mechanism for the import trigger and fixed that one path
      // with reloadAllSettings(); the header-toggle trigger has the same
      // shape and was not covered. Sending dirtyKeys closes both, and is
      // less code than the filter it replaces.
      const payload = Object.fromEntries(dirtyKeys.map(k => [k, snapshot[k]]));

      // Nothing to do — the SaveBar is disabled when clean, but a double
      // click or an Enter keypress can still land here.
      if (dirtyKeys.length === 0) {
        setStatus("saved");
        return;
      }

      const r = await fetch(`${api}/api/settings/`, {
        method:  "PUT",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(payload),
      });
      if (r.ok) {
        setStatus("saved");
        // Re-read rather than trusting the local snapshot. The unsent keys
        // are exactly the ones that may have been changed elsewhere, so this
        // is what makes the page agree with the backend again instead of
        // continuing to render step 1's stale values.
        await loadSettings();
      } else {
        setStatus("error");
      }
    } catch (err) {
      console.error("Saving settings failed", err);
      setStatus("error");
    }
  };

  const set = (k, v) => setValues(prev => ({ ...prev, [k]: v }));

  // schema grouped by declared group name
  const groupsMap = schema.reduce((acc, field) => {
    const g = field.group || "General";
    acc[g] ??= [];
    acc[g].push(field);
    return acc;
  }, {});

  const renderGroup = (groupName, first) => {
    const fields = groupsMap[groupName] || [];
    if (fields.length === 0) return null;
    return (
      <div key={groupName}>
      <SectionHeader label={groupName} first={first} />
      {fields.map(field => {
        // dry_run_mode and auto_start_jobs are owned by the app-level state
        // that the header renders from, not by this page's loaded snapshot.
        // Rendering them from `live` and committing through the SAME action
        // the header calls means the two controls cannot disagree: there is
        // one value, not two copies to keep in step.
        //
        // Previously they were ordinary staged fields, so changing one here
        // updated the header only after a full page reload — the page
        // remounts on every tab change and refetches, but useAppData sits
        // above the page switch and never does.
        const live = liveToggles[field.key];
        return (
          <FieldRow
          key={field.key}
          field={field}
          value={live ? live.value : values[field.key]}
          onChange={live ? live.onToggle : (v => set(field.key, v))}
          isMobile={isMobile}
          immediate={!!live}
          />
        );
      })}
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
        <MaintenanceSection api={api} toast={toast} reloadKey={reloadKey} />
        <LogViewer api={api} toast={toast} />
        </>
      );
    }
    if (cat.custom === "backup") {
      return (
        <>
        <BackupRestoreSection api={api} toast={toast} onImported={reloadAllSettings} />
        <FullBackupSection api={api} toast={toast} />
        <DangerZone api={api} toast={toast} />
        </>
      );
    }
    if (cat.custom === "appearance") {
      return <AppearanceSection />;
    }
    if (schema.length === 0) {
      // Two different situations shared one message before. The fetch is a
      // one-shot with no retry, so once it has failed this placeholder is
      // permanent — telling the user to "connect to the backend" implies a
      // wait that will never end. Reloading is the only actual recovery.
      return (
        <div style={{ color: palette.muted, fontSize: type.size.md, textAlign: "center", padding: space.xxxl }}>
        {loadError
          ? "Couldn't load settings from the backend. Reload the page to try again."
          : "Connect to the backend to load settings…"}
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
      <div style={{ maxWidth: 700, margin: "0 auto", padding: `${space.xl}px ${space.xl}px ${space.giant}px` }}>
      <div style={{ position: "sticky", top: 0, zIndex: LAYER.stickyNav, background: palette.bg }}>
      <div style={{ padding: `${space.hair}px 0 ${space.sm}px` }}>
      <NavDropdown active={active} onSelect={setActive} />
      </div>
      {saveBar}
      </div>
      <div style={{ marginTop: space.xs }}>{renderCategory()}</div>
      </div>
    );
  }

  // ── Desktop: sticky sidebar + content with a sticky save bar ──────────────
  return (
    <div style={{ maxWidth: 940, margin: "0 auto", padding: `${space.huge}px ${space.huge}px ${space.mega}px`, display: "flex", gap: space.max }}>
    <NavSidebar active={active} onSelect={setActive} dirty={isDirty} />
    <div style={{ flex: 1, minWidth: 0 }}>
    <div style={{ position: "sticky", top: 0, zIndex: LAYER.stickySaveBar }}>
    {saveBar}
    </div>
    <div style={{ marginTop: space.xs }}>{renderCategory()}</div>
    </div>
    </div>
  );
};
