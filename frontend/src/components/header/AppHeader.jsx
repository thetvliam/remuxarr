import { useState } from "react";
import { C } from "../../constants";
import { LED } from "../atoms/LED";
import { ApiBar } from "./ApiBar";

/* ═══════════════════════════════════════════════════════════════════════════
 * APP HEADER
 * Desktop: single bar — logo, nav, controls, WS indicator all inline.
 * Mobile:  two-row layout.
 *   Row 1 (always visible): logo, ⚙ button, WS indicator, ☰ hamburger.
 *   Drawer (toggled by ☰): nav links + action controls as full-width rows.
 * The drawer closes when any nav link or control is tapped, or when the
 * user taps the backdrop overlay below it.
 ═ ═*═════════════════════════════════════════════════════════════════════════ */

const NAV_ITEMS = [
  { k: "dashboard", l: "DASHBOARD" },
{ k: "settings",  l: "SETTINGS"  },
{ k: "review",    l: "REVIEW",    alertable: true },
{ k: "forge",     l: "FORGE"     },
];

export const AppHeader = ({
  page, setPage,
  reviewCount,
  api, setApi, showApiBar, setShowApiBar,
  dryRun, onToggleDryRun,
  autoStart, onToggleAutoStart,
  workerPaused, onTogglePause,
  scanning, scanProgress, onTriggerScan, onCancelScan,
  wsConnected,
  isMobile,
}) => {
  const [drawerOpen, setDrawerOpen] = useState(false);

  const closeDrawer = () => setDrawerOpen(false);

  const navLabel = (n) =>
  n.alertable && reviewCount > 0 ? `${n.l} (${reviewCount})` : n.l;

  const scanLabel = scanning
  ? (scanProgress ? `✕ STOP (${scanProgress.scanned}/${scanProgress.total})` : "✕ STOP SCAN")
  : "↻ SCAN";

  // ── Desktop layout ────────────────────────────────────────────────────────
  if (!isMobile) {
    return (
      <header style={{
        height: 46,
        display: "flex",
        alignItems: "center",
        padding: "0 18px",
        background: C.card,
        borderBottom: `1px solid ${C.border}`,
        flexShrink: 0,
        gap: 0,
      }}>
      {/* Logo */}
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginRight: 22 }}>
      <div style={{
        width: 24, height: 24,
        background: C.amber,
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
      <span style={{ color: "#000", fontSize: 12, fontWeight: 900 }}>R</span>
      </div>
      <span style={{ color: C.text, fontSize: 12, fontWeight: 700, letterSpacing: "0.18em" }}>
      REMUXARR
      </span>
      </div>

      {/* Nav links */}
      {NAV_ITEMS.map(n => (
        <button
        key={n.k}
        onClick={() => setPage(n.k)}
        style={{
          height: 46,
          padding: "0 14px",
          background: "transparent",
          border: "none",
          borderBottom: page === n.k
          ? `2px solid ${n.alertable && reviewCount > 0 ? C.yellow : C.amber}`
          : "2px solid transparent",
          color: page === n.k
          ? (n.alertable && reviewCount > 0 ? C.yellow : C.amber)
          : C.dim,
          fontSize: 9,
          fontFamily: "inherit",
          letterSpacing: "0.14em",
          fontWeight: 700,
          cursor: "pointer",
        }}
        >
        {navLabel(n)}
        </button>
      ))}

      <div style={{ flex: 1 }} />

      {/* Inline API configurator */}
      {showApiBar && (
        <div style={{ marginRight: 12 }}>
        <ApiBar current={api} onSave={(v) => { setApi(v); setShowApiBar(false); }} />
        </div>
      )}

      {/* ⚙ API URL */}
      <button
      onClick={() => setShowApiBar(v => !v)}
      title={`API: ${api}`}
      style={{
        background: "none", border: "none",
        color: showApiBar ? C.amber : C.dim,
        fontSize: 14, cursor: "pointer",
        padding: "0 7px", fontFamily: "inherit",
      }}
      >⚙</button>

      {/* Dry-run */}
      <button onClick={onToggleDryRun} style={{
        padding: "3px 10px", marginRight: 8,
        background: dryRun ? C.yellow + "20" : "transparent",
        border: `1px solid ${dryRun ? C.yellow : C.border}`,
        color: dryRun ? C.yellow : C.dim,
        fontSize: 9, fontFamily: "inherit", letterSpacing: "0.1em", cursor: "pointer",
      }}>
      {dryRun ? "◆ DRY RUN" : "◇ DRY RUN"}
      </button>

      {/* Auto-start */}
      <button
      onClick={onToggleAutoStart}
      title={autoStart
        ? "Auto-start enabled — files process immediately after a scan"
        : "Auto-start disabled — files queue but won't process until you click Resume"}
        style={{
          padding: "3px 10px", marginRight: 8,
          background: autoStart ? "transparent" : C.blue + "18",
          border: `1px solid ${autoStart ? C.border : C.blue}`,
          color: autoStart ? C.dim : C.blue,
          fontSize: 9, fontFamily: "inherit", letterSpacing: "0.1em", cursor: "pointer",
        }}
        >
        {autoStart ? "⚡ AUTO" : "⏸ MANUAL"}
        </button>

        {/* Pause / Resume */}
        <button
        onClick={onTogglePause}
        title={workerPaused ? "Resume processing" : "Pause processing — finish the current job then stop"}
        style={{
          padding: "3px 10px", marginRight: 8,
          background: workerPaused ? C.yellow + "20" : "transparent",
          border: `1px solid ${workerPaused ? C.yellow : C.border}`,
          color: workerPaused ? C.yellow : C.dim,
          fontSize: 9, fontFamily: "inherit", letterSpacing: "0.1em", cursor: "pointer",
          animation: workerPaused ? "ledPulse 2s ease-in-out infinite" : "none",
        }}
        >
        {workerPaused ? "▶ RESUME" : "⏸ PAUSE"}
        </button>

        {/* Scan */}
        <button
        onClick={scanning ? onCancelScan : onTriggerScan}
        style={{
          padding: "3px 12px", marginRight: 16,
          background: "transparent",
          border: `1px solid ${scanning ? C.red : C.border}`,
          color: scanning ? C.red : C.dim,
          fontSize: 9, fontFamily: "inherit", letterSpacing: "0.1em",
          cursor: "pointer",
          animation: scanning ? "ledPulse 1.5s ease-in-out infinite" : "none",
        }}
        >
        {scanLabel}
        </button>

        {/* WS status */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <LED color={wsConnected ? C.green : C.red} pulse={wsConnected} size={7} />
        <span style={{ color: C.dim, fontSize: 9, letterSpacing: "0.08em" }}>
        {wsConnected ? "LIVE" : "OFFLINE"}
        </span>
        </div>
        </header>
    );
  }

  // ── Mobile layout ─────────────────────────────────────────────────────────
  return (
    <div style={{ position: "relative", flexShrink: 0 }}>
    {/* Row 1 — always visible */}
    <header style={{
      height: 46,
      display: "flex",
      alignItems: "center",
      padding: "0 14px",
      background: C.card,
      borderBottom: `1px solid ${C.border}`,
      gap: 8,
      zIndex: 600,
      position: "relative",
    }}>
    {/* Logo mark only — no text, saves space */}
    <div style={{
      width: 24, height: 24, flexShrink: 0,
      background: C.amber,
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
    <span style={{ color: "#000", fontSize: 12, fontWeight: 900 }}>R</span>
    </div>

    {/* Current page label */}
    <span style={{
      color: C.amber, fontSize: 9, fontWeight: 700,
      letterSpacing: "0.18em", flex: 1,
    }}>
    {NAV_ITEMS.find(n => n.k === page)?.l ?? "REMUXARR"}
    {page === "review" && reviewCount > 0 ? ` (${reviewCount})` : ""}
    </span>

    {/* WS status — compact */}
    <LED color={wsConnected ? C.green : C.red} pulse={wsConnected} size={7} />

    {/* ⚙ API */}
    <button
    onClick={() => setShowApiBar(v => !v)}
    style={{
      background: "none", border: "none",
      color: showApiBar ? C.amber : C.dim,
      fontSize: 16, cursor: "pointer",
      padding: "0 4px", fontFamily: "inherit",
    }}
    >⚙</button>

    {/* ☰ Hamburger */}
    <button
    onClick={() => setDrawerOpen(v => !v)}
    style={{
      background: "none", border: "none",
      color: drawerOpen ? C.amber : C.dim,
      fontSize: 18, cursor: "pointer",
      padding: "0 4px", fontFamily: "inherit",
      lineHeight: 1,
    }}
    >
    {drawerOpen ? "✕" : "☰"}
    </button>
    </header>

    {/* Drawer */}
    {drawerOpen && (
      <>
      {/* Backdrop — closes drawer on tap */}
      <div
      onClick={closeDrawer}
      style={{
        position: "fixed",
        inset: 0,
        top: 46,
        zIndex: 490,
        background: "transparent",
      }}
      />

      {/* Drawer panel */}
      <div style={{
        position: "absolute",
        top: "100%",
        left: 0,
        right: 0,
        background: C.card,
        borderBottom: `1px solid ${C.border}`,
        zIndex: 500,
        boxShadow: "0 4px 16px #00000066",
      }}>
      {/* API bar (when open) */}
      {showApiBar && (
        <div style={{ padding: "10px 14px", borderBottom: `1px solid ${C.border}` }}>
        <ApiBar
        current={api}
        onSave={(v) => { setApi(v); setShowApiBar(false); }}
        />
        </div>
      )}

      {/* Nav links */}
      {NAV_ITEMS.map(n => {
        const active = page === n.k;
        const alert  = n.alertable && reviewCount > 0;
        return (
          <button
          key={n.k}
          onClick={() => { setPage(n.k); closeDrawer(); }}
          style={{
            display: "block",
            width: "100%",
            textAlign: "left",
            padding: "13px 18px",
            background: active ? (alert ? C.yellow + "12" : C.amber + "12") : "transparent",
                border: "none",
                borderLeft: `3px solid ${active ? (alert ? C.yellow : C.amber) : "transparent"}`,
                borderBottom: `1px solid ${C.border}`,
                color: active ? (alert ? C.yellow : C.amber) : C.dim,
                fontSize: 11,
                fontFamily: "inherit",
                letterSpacing: "0.12em",
                fontWeight: 700,
                cursor: "pointer",
          }}
          >
          {navLabel(n)}
          </button>
        );
      })}

      {/* Action controls */}
      <div style={{ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
      {/* Dry run */}
      <button
      onClick={() => { onToggleDryRun(); closeDrawer(); }}
      style={{
        padding: "10px 14px", textAlign: "left",
        background: dryRun ? C.yellow + "20" : "transparent",
        border: `1px solid ${dryRun ? C.yellow : C.border}`,
        color: dryRun ? C.yellow : C.dim,
        fontSize: 10, fontFamily: "inherit",
        letterSpacing: "0.1em", cursor: "pointer",
      }}
      >
      {dryRun ? "◆ DRY RUN  — tap to disable" : "◇ DRY RUN  — tap to enable"}
      </button>

      {/* Auto-start */}
      <button
      onClick={() => { onToggleAutoStart(); closeDrawer(); }}
      style={{
        padding: "10px 14px", textAlign: "left",
        background: autoStart ? "transparent" : C.blue + "18",
        border: `1px solid ${autoStart ? C.border : C.blue}`,
        color: autoStart ? C.dim : C.blue,
        fontSize: 10, fontFamily: "inherit",
        letterSpacing: "0.1em", cursor: "pointer",
      }}
      >
      {autoStart ? "⚡ AUTO-START  — tap to disable" : "⏸ MANUAL  — tap to enable auto-start"}
      </button>

      {/* Pause / Resume */}
      <button
      onClick={() => { onTogglePause(); closeDrawer(); }}
      style={{
        padding: "10px 14px", textAlign: "left",
        background: workerPaused ? C.yellow + "20" : "transparent",
        border: `1px solid ${workerPaused ? C.yellow : C.border}`,
        color: workerPaused ? C.yellow : C.dim,
        fontSize: 10, fontFamily: "inherit",
        letterSpacing: "0.1em", cursor: "pointer",
        animation: workerPaused ? "ledPulse 2s ease-in-out infinite" : "none",
      }}
      >
      {workerPaused ? "▶ RESUME  — tap to resume processing" : "⏸ PAUSE  — tap to pause after current job"}
      </button>

      {/* Scan */}
      <button
      onClick={() => {
        if (scanning) { onCancelScan(); }
        else { onTriggerScan(); closeDrawer(); }
      }}
      style={{
        padding: "10px 14px", textAlign: "left",
        background: "transparent",
        border: `1px solid ${scanning ? C.red : C.border}`,
        color: scanning ? C.red : C.dim,
        fontSize: 10, fontFamily: "inherit",
        letterSpacing: "0.1em",
        cursor: "pointer",
        animation: scanning ? "ledPulse 1.5s ease-in-out infinite" : "none",
      }}
      >
      {scanLabel}
      </button>
      </div>
      </div>
      </>
    )}
    </div>
  );
};
