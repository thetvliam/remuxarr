import { useState } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
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
 ═ * * ═*═════════════════════════════════════════════════════════════════════════ */

const NAV_ITEMS = [
  { k: "dashboard", l: "DASHBOARD" },
{ k: "settings",  l: "SETTINGS"  },
{ k: "review",    l: "REVIEW",    alertable: true },
{ k: "forge",     l: "FORGE"     },
];

/* ── Logo ────────────────────────────────────────────────────────────────────
 * Served from frontend/public/, which Vite copies to the dist root at build
 * time (and app.main's static handler serves from there), so these absolute
 * paths work in dev and in the container alike.
 *
 *   variant="full" → /logo-name.svg   icon + wordmark (~4:1), desktop header
 *   variant="mark" → /logo.svg        icon only (1:1),        mobile header
 *
 * Height is fixed and width follows the file's own aspect ratio, so the
 * lockup can be re-exported at a different ratio without touching this code.
 * If a file is missing or fails to load, the original CSS placeholder renders
 * instead — the header degrades to the old look rather than a broken image.
 */
const LOGO_SRC = { mark: "/logo.svg", full: "/logo-name.svg" };

const Logo = ({ variant = "mark", height = 24 }) => {
  const { palette, type, space, surface } = useTheme();
  const [failed, setFailed] = useState(false);

  if (failed) {
    return (
      <div style={{ display: "flex", alignItems: "center", gap: space.xs + 3 }}>
      <div style={{
        width: height, height, flexShrink: 0,
        background: palette.amber,
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
      <span style={{ color: surface.logoInk, fontSize: type.size.base, fontWeight: type.weight.black }}>R</span>
      </div>
      {variant === "full" && (
        <span style={{ color: palette.text, fontSize: type.size.base, fontWeight: type.weight.bold, letterSpacing: type.tracking.max }}>
        REMUXARR
        </span>
      )}
      </div>
    );
  }

  return (
    <img
    src={LOGO_SRC[variant]}
    alt="Remuxarr"
    draggable={false}
    onError={() => setFailed(true)}
    style={{
      height,
      width: variant === "mark" ? height : "auto",
      flexShrink: 0,
      display: "block",
      userSelect: "none",
    }}
    />
  );
};

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
  const { palette, type, space, radius, size, surface } = useTheme();
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
        height: size.headerHeight,
        display: "flex",
        alignItems: "center",
        padding: `0 ${space.xxl}px`,
        background: palette.card,
        borderBottom: `1px solid ${palette.border}`,
        flexShrink: 0,
        gap: 0,
      }}>
      {/* Logo — icon + wordmark lockup */}
      <div style={{ display: "flex", alignItems: "center", marginRight: space.huge }}>
      <Logo variant="full" />
      </div>

      {/* Nav links */}
      {NAV_ITEMS.map(n => (
        <button
        key={n.k}
        onClick={() => setPage(n.k)}
        style={{
          height: size.headerHeight,
          padding: `0 ${space.xl}px`,
          background: "transparent",
          border: "none",
          borderBottom: page === n.k
          ? `${size.accentThin}px solid ${n.alertable && reviewCount > 0 ? palette.yellow : palette.amber}`
          : `${size.accentThin}px solid transparent`,
          color: page === n.k
          ? (n.alertable && reviewCount > 0 ? palette.yellow : palette.amber)
          : palette.dim,
          fontSize: type.size.xs,
          fontFamily: type.family,
          letterSpacing: type.tracking.widest,
          fontWeight: type.weight.bold,
          cursor: "pointer",
        }}
        >
        {navLabel(n)}
        </button>
      ))}

      <div style={{ flex: 1 }} />

      {/* Inline API configurator */}
      {showApiBar && (
        <div style={{ marginRight: space.lg }}>
        <ApiBar current={api} onSave={(v) => { setApi(v); setShowApiBar(false); }} />
        </div>
      )}

      {/* ⚙ API URL */}
      <button
      onClick={() => setShowApiBar(v => !v)}
      title={`API: ${api}`}
      style={{
        background: "none", border: "none",
        color: showApiBar ? palette.amber : palette.dim,
        fontSize: type.size.xl, cursor: "pointer",
        padding: `0 ${space.xs + 2}px`, fontFamily: type.family,
      }}
      >⚙</button>

      {/* Dry-run */}
      <button onClick={onToggleDryRun} style={{
        padding: `${space.xxs}px ${space.md}px`, marginRight: space.sm,
        background: dryRun ? alpha(palette.yellow, ALPHA.mild) : "transparent",
            border: `1px solid ${dryRun ? palette.yellow : palette.border}`,
            borderRadius: radius.sm,
            color: dryRun ? palette.yellow : palette.dim,
            fontSize: type.size.xs, fontFamily: type.family, letterSpacing: type.tracking.wide, cursor: "pointer",
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
          padding: `${space.xxs}px ${space.md}px`, marginRight: space.sm,
          background: autoStart ? "transparent" : alpha(palette.blue, ALPHA.low),
            border: `1px solid ${autoStart ? palette.border : palette.blue}`,
            borderRadius: radius.sm,
            color: autoStart ? palette.dim : palette.blue,
            fontSize: type.size.xs, fontFamily: type.family, letterSpacing: type.tracking.wide, cursor: "pointer",
        }}
        >
        {autoStart ? "⚡ AUTO" : "⏸ MANUAL"}
        </button>

        {/* Pause / Resume */}
        <button
        onClick={onTogglePause}
        title={workerPaused ? "Resume processing" : "Pause processing — finish the current job then stop"}
        style={{
          padding: `${space.xxs}px ${space.md}px`, marginRight: space.sm,
          background: workerPaused ? alpha(palette.yellow, ALPHA.mild) : "transparent",
            border: `1px solid ${workerPaused ? palette.yellow : palette.border}`,
            borderRadius: radius.sm,
            color: workerPaused ? palette.yellow : palette.dim,
            fontSize: type.size.xs, fontFamily: type.family, letterSpacing: type.tracking.wide, cursor: "pointer",
            animation: workerPaused ? "ledPulse 2s ease-in-out infinite" : "none",
        }}
        >
        {workerPaused ? "▶ RESUME" : "⏸ PAUSE"}
        </button>

        {/* Scan */}
        <button
        onClick={scanning ? onCancelScan : onTriggerScan}
        style={{
          padding: `${space.xxs}px ${space.lg}px`, marginRight: space.xl,
          background: "transparent",
          border: `1px solid ${scanning ? palette.red : palette.border}`,
          borderRadius: radius.sm,
          color: scanning ? palette.red : palette.dim,
          fontSize: type.size.xs, fontFamily: type.family, letterSpacing: type.tracking.wide,
          cursor: "pointer",
          animation: scanning ? "ledPulse 1.5s ease-in-out infinite" : "none",
        }}
        >
        {scanLabel}
        </button>

        {/* WS status */}
        <div style={{ display: "flex", alignItems: "center", gap: space.xs }}>
        <LED color={wsConnected ? palette.green : palette.red} pulse={wsConnected} size={size.ledSize} />
        <span style={{ color: palette.dim, fontSize: type.size.xs, letterSpacing: type.tracking.normal }}>
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
      height: size.headerHeight,
      display: "flex",
      alignItems: "center",
      padding: `0 ${space.xl}px`,
      background: palette.card,
      borderBottom: `1px solid ${palette.border}`,
      gap: space.sm,
      zIndex: 600,
      position: "relative",
    }}>
    {/* Logo mark only — no wordmark, saves space */}
    <Logo variant="mark" />

    {/* Current page label. minWidth:0 + ellipsis so it yields space to the
      controls on its right instead of pushing them off a narrow screen. */}
      <span style={{
        color: palette.amber, fontSize: type.size.xs, fontWeight: type.weight.bold,
        letterSpacing: type.tracking.max, flex: 1,
        minWidth: 0, overflow: "hidden",
        textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
      {NAV_ITEMS.find(n => n.k === page)?.l ?? "REMUXARR"}
      {page === "review" && reviewCount > 0 ? ` (${reviewCount})` : ""}
      </span>

      {/* Scan — kept in the always-visible row rather than the drawer. It's
        the control reached for most often, and burying the app's primary
        action two taps deep (open drawer, then tap) made it feel missing.
        Doubles as the scan-in-progress indicator: while a scan runs it
        shows live counts and becomes the cancel button, so mobile users
        can see and stop a scan without opening anything. */}
        <button
        onClick={scanning ? onCancelScan : onTriggerScan}
        title={scanning ? "Cancel the running scan" : "Scan library now"}
        style={{
          flexShrink: 0,
          padding: `${space.xxs}px ${space.sm}px`,
          background: scanning ? alpha(palette.red, ALPHA.low) : "transparent",
          border: `1px solid ${scanning ? palette.red : palette.border}`,
          borderRadius: radius.sm,
          color: scanning ? palette.red : palette.dim,
          fontSize: type.size.xs, fontFamily: type.family, fontWeight: type.weight.bold,
          letterSpacing: type.tracking.normal, cursor: "pointer",
          whiteSpace: "nowrap",
          animation: scanning ? "ledPulse 1.5s ease-in-out infinite" : "none",
        }}
        >
        {scanning
          ? (scanProgress ? `✕ ${scanProgress.scanned}/${scanProgress.total}` : "✕ STOP")
          : "⟳ SCAN"}
          </button>

          {/* WS status — compact */}
          <LED color={wsConnected ? palette.green : palette.red} pulse={wsConnected} size={size.ledSize} />

          {/* ⚙ API */}
          <button
          onClick={() => setShowApiBar(v => !v)}
          style={{
            background: "none", border: "none",
            color: showApiBar ? palette.amber : palette.dim,
            fontSize: type.size.h2, cursor: "pointer",
            padding: `0 ${space.xxs}px`, fontFamily: type.family,
          }}
          >⚙</button>

          {/* ☰ Hamburger */}
          <button
          onClick={() => setDrawerOpen(v => !v)}
          style={{
            background: "none", border: "none",
            color: drawerOpen ? palette.amber : palette.dim,
            fontSize: type.size.h1, cursor: "pointer",
            padding: `0 ${space.xxs}px`, fontFamily: type.family,
            lineHeight: type.leading.none,
          }}
          >
          {drawerOpen ? "✕" : "☰"}
          </button>
          </header>

          {/* API URL bar — its own row directly under the header, NOT inside the
            drawer. It used to live in the drawer, so tapping ⚙ with the drawer
            closed toggled state that nothing was rendering: the button looked
            broken, and the input only appeared if you happened to open the
            hamburger afterwards. Out here it behaves like the desktop layout —
            ⚙ shows and hides it directly, drawer open or closed. */}
            {showApiBar && (
              <div style={{
                padding: `${space.md}px ${space.xl}px`,
                background: palette.card,
                borderBottom: `1px solid ${palette.border}`,
                position: "relative",
                zIndex: 550,
              }}>
              <ApiBar
              current={api}
              onSave={(v) => { setApi(v); setShowApiBar(false); }}
              />
              </div>
            )}

            {/* Drawer */}
            {drawerOpen && (
              <>
              {/* Backdrop — closes drawer on tap */}
              <div
              onClick={closeDrawer}
              style={{
                position: "fixed",
                inset: 0,
                top: size.headerHeight,
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
                background: palette.card,
                borderBottom: `1px solid ${palette.border}`,
                zIndex: 500,
                boxShadow: surface.drawerShadow,
              }}>
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
                    padding: `${space.lg}px ${space.xxl}px`,
                    background: active ? (alert ? alpha(palette.yellow, ALPHA.trace) : alpha(palette.amber, ALPHA.trace)) : "transparent",
                        border: "none",
                        borderLeft: `${size.accentWidth}px solid ${active ? (alert ? palette.yellow : palette.amber) : "transparent"}`,
                        borderBottom: `1px solid ${palette.border}`,
                        color: active ? (alert ? palette.yellow : palette.amber) : palette.dim,
                        fontSize: type.size.md,
                        fontFamily: type.family,
                        letterSpacing: type.tracking.wider,
                        fontWeight: type.weight.bold,
                        cursor: "pointer",
                  }}
                  >
                  {navLabel(n)}
                  </button>
                );
              })}

              {/* Action controls */}
              <div style={{ padding: `${space.md}px ${space.xl}px`, display: "flex", flexDirection: "column", gap: space.sm }}>
              {/* Dry run */}
              <button
              onClick={() => { onToggleDryRun(); closeDrawer(); }}
              style={{
                padding: `${space.md}px ${space.xl}px`, textAlign: "left",
                background: dryRun ? alpha(palette.yellow, ALPHA.mild) : "transparent",
                            border: `1px solid ${dryRun ? palette.yellow : palette.border}`,
                            borderRadius: radius.sm,
                            color: dryRun ? palette.yellow : palette.dim,
                            fontSize: type.size.sm, fontFamily: type.family,
                            letterSpacing: type.tracking.wide, cursor: "pointer",
              }}
              >
              {dryRun ? "◆ DRY RUN  — tap to disable" : "◇ DRY RUN  — tap to enable"}
              </button>

              {/* Auto-start */}
              <button
              onClick={() => { onToggleAutoStart(); closeDrawer(); }}
              style={{
                padding: `${space.md}px ${space.xl}px`, textAlign: "left",
                background: autoStart ? "transparent" : alpha(palette.blue, ALPHA.low),
                            border: `1px solid ${autoStart ? palette.border : palette.blue}`,
                            borderRadius: radius.sm,
                            color: autoStart ? palette.dim : palette.blue,
                            fontSize: type.size.sm, fontFamily: type.family,
                            letterSpacing: type.tracking.wide, cursor: "pointer",
              }}
              >
              {autoStart ? "⚡ AUTO-START  — tap to disable" : "⏸ MANUAL  — tap to enable auto-start"}
              </button>

              {/* Pause / Resume */}
              <button
              onClick={() => { onTogglePause(); closeDrawer(); }}
              style={{
                padding: `${space.md}px ${space.xl}px`, textAlign: "left",
                background: workerPaused ? alpha(palette.yellow, ALPHA.mild) : "transparent",
                            border: `1px solid ${workerPaused ? palette.yellow : palette.border}`,
                            borderRadius: radius.sm,
                            color: workerPaused ? palette.yellow : palette.dim,
                            fontSize: type.size.sm, fontFamily: type.family,
                            letterSpacing: type.tracking.wide, cursor: "pointer",
                            animation: workerPaused ? "ledPulse 2s ease-in-out infinite" : "none",
              }}
              >
              {workerPaused ? "▶ RESUME  — tap to resume processing" : "⏸ PAUSE  — tap to pause after current job"}
              </button>

              {/* No Scan row here — scanning moved to the always-visible header row,
                so duplicating it in the drawer would just be two controls for the
                same action. */}
                </div>
                </div>
                </>
            )}
            </div>
  );
};
