import { useState, useEffect, useRef } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { fmtClock } from "../../utils";

/* ═══════════════════════════════════════════════════════════════════════════
 * LOG VIEWER
 * Polls GET /api/logs every 3 seconds while mounted. Mounted only by the
 * Maintenance & Logs category of Settings, so polling starts and stops on
 * category switch, not just on leaving the page — the other categories
 * never start it. The theme editor's Settings preview mounts it too.
 *
 * Level filter is client-side — all 200 most recent records are fetched
 * and the selected minimum level is applied locally, so toggling is instant
 * without a new network round-trip.
 *
 * Auto-scroll keeps the list pinned to the newest entry.  It disengages
 * automatically when the user scrolls up, and re-engages when they scroll
 * back to the bottom.
 ═══════════════════════════════════════════════════════════════════════════ */

// CRITICAL ranks above ERROR. It was equal, so the two were
// indistinguishable to the filter: selecting ERROR included CRITICAL with
// no way to isolate it, and adding a CRITICAL option to LEVELS would have
// shown ERROR lines too.
const LEVEL_ORDER = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };
const LEVELS = ["ALL", "INFO", "WARNING", "ERROR"];

export const LogViewer = ({ api, toast }) => {
  const { palette, type, space, radius, surface, levelColor } = useTheme();
  const [allRecords,  setAllRecords]  = useState([]);
  const [levelFilter, setLevelFilter] = useState("INFO");
  const [autoScroll,  setAutoScroll]  = useState(true);
  const [clearing,    setClearing]    = useState(false);

  const scrollRef    = useRef(null);
  const atBottomRef  = useRef(true);  // tracks whether user is at the bottom

  // ── Polling ─────────────────────────────────────────────────────────────
  useEffect(() => {
    /* `seq` ties a response to the request that asked for it. The interval
     * does not wait for the previous poll, so at 3s against a slow backend
     * two are in flight together and the older one landing last put stale
     * lines back on screen.
     *
     * The status check is the other half. fetch does not reject on an HTTP
     * error, so an error body reached `d.records || []`, found no records on
     * it and emptied the view — a single 500 blanked the log while the
     * backend was still running. Returning early leaves the lines alone and
     * the next tick retries, which is what the catch already did for a
     * network failure.
     *
     * No unmount guard, deliberately. cleanup stops the interval, so the most
     * that outlives this component is the one request already in flight, and
     * on React 18 its setState is a silent no-op — verified, not assumed: a
     * poll resolved after unmount logs nothing. A flag for it could not be
     * tested, and an untestable guard is one a later reader cannot tell is
     * still doing anything. */
    let latest = 0;
    const poll = async () => {
      const seq = ++latest;
      try {
        const r = await fetch(`${api}/api/logs/?limit=200`);
        if (!r.ok) return;
        const d = await r.json();
        if (seq !== latest) return;
        setAllRecords(d.records || []);
      } catch {
        // Keep what is on screen; the next tick tries again.
      }
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => clearInterval(id);
  }, [api]);

  // ── Auto-scroll ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (autoScroll && atBottomRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [allRecords, autoScroll]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    // Consider "at bottom" if within 40px of the scrollable end
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (atBottomRef.current) setAutoScroll(true);
    else setAutoScroll(false);
  };

    // ── Level filter (client-side) ───────────────────────────────────────────
    const records = levelFilter === "ALL"
    ? allRecords
    : allRecords.filter(r =>
    (LEVEL_ORDER[r.level] ?? 0) >= (LEVEL_ORDER[levelFilter] ?? 0)
    );

    // ── Clear ────────────────────────────────────────────────────────────────
    const clearLogs = async () => {
      setClearing(true);
      // try/finally with no catch let a network failure escape an async click
      // handler as an unhandled rejection, and the missing r.ok meant a 500
      // still emptied the on-screen list while the server kept every line.
      try {
        const r = await fetch(`${api}/api/logs/`, { method: "DELETE" });
        if (!r.ok) {
          toast?.("Failed to clear logs", "error");
          return;
        }
        setAllRecords([]);
      } catch (err) {
          console.error("Clear logs failed", err);
        toast?.("Could not reach the server", "error");
      } finally {
        setClearing(false);
      }
    };

    return (
      <div style={{ marginTop: space.giant, paddingTop: space.huge, borderTop: `1px solid ${palette.border}` }}>
      {/* Section header */}
      <div style={{
        color: palette.amber,
        fontSize: type.size.xs,
        letterSpacing: type.tracking.max,
        fontWeight: type.weight.bold,
        marginBottom: space.xl,
      }}>
      APPLICATION LOGS
      </div>

      {/* Controls */}
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: space.sm,
        marginBottom: space.sm,
        flexWrap: "wrap",
      }}>
      {/* Level filter */}
      <div style={{ display: "flex" }}>
      {LEVELS.map((l, i) => {
        const active = levelFilter === l;
        /* The same map the log rows below use, rather than a second copy
         * of it. The inline ternary this replaces listed INFO, WARNING
         * and ERROR and sent everything else to red — so it agreed with
         * theme.levelColor only by coincidence, and adding DEBUG or
         * CRITICAL to LEVELS would have coloured the new filter red
         * while its rows rendered correctly. "ALL" is not a level, so it
         * falls through to muted. */
        const color = levelColor[l] || palette.muted;
        return (
          <button
          key={l}
          onClick={() => setLevelFilter(l)}
          style={{
            padding: `${space.xxs}px ${space.md}px`,
            background: active ? `${alpha(color, ALPHA.medium)}` : "transparent",
                // No borderRadius: these are a segmented control. Every
                // button but the last drops its right border so its
                // neighbour's left border serves both, and rounding a
                // segment would round the edges it shares, breaking the
                // strip into pieces on any theme with a real radius.
                // Only the group's two outer corners should curve, which
                // needs a clipping wrapper rather than per-segment corners.
                border: `1px solid ${active ? color : palette.border}`,
                borderRight: i < LEVELS.length - 1 ? "none" : undefined,
                color: active ? color : palette.dim,
                fontSize: type.size.xs,
                fontFamily: type.family,
                letterSpacing: type.tracking.normal,
                cursor: "pointer",
          }}
          >
          {l}
          </button>
        );
      })}
      </div>

      {/* Auto-scroll toggle */}
      <button
      onClick={() => {
        /* Turning it on jumps to the bottom and clears the
         * at-bottom flag. The scroll effect requires BOTH autoScroll
         * and atBottomRef, and handleScroll sets atBottomRef false the
         * moment the user scrolls up — which is the only way anyone
         * ever turns this off. So clicking it back on lit the button
         * and did nothing, because only manually scrolling to the
         * bottom could reset the flag. The control was inert in
         * exactly the situation it exists for. */
        setAutoScroll((a) => {
          const next = !a;
          if (next && scrollRef.current) {
            atBottomRef.current = true;
            scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
          }
          return next;
        });
      }}
      title="Toggle auto-scroll to newest entry"
      style={{
        padding: `${space.xxs}px ${space.md}px`,
        background: autoScroll ? `${alpha(palette.blue, ALPHA.medium)}` : "transparent",
            border: `1px solid ${autoScroll ? palette.blue : palette.border}`,
            borderRadius: radius.sm,
            color: autoScroll ? palette.blue : palette.dim,
            fontSize: type.size.xs,
            fontFamily: type.family,
            letterSpacing: type.tracking.normal,
            cursor: "pointer",
      }}
      >
      ↓ AUTO-SCROLL
      </button>

      {/* Record count */}
      <span style={{ color: palette.dim, fontSize: type.size.sm, marginLeft: space.xxs }}>
      {records.length} record{records.length === 1 ? "" : "s"}
      </span>

      {/* Clear */}
      <button
      onClick={clearLogs}
      disabled={clearing || allRecords.length === 0}
      style={{
        marginLeft: "auto",
        padding: `${space.xxs}px ${space.md}px`,
        background: "transparent",
        border: `1px solid ${palette.border}`,
        borderRadius: radius.sm,
        color: palette.dim,
        fontSize: type.size.xs,
        fontFamily: type.family,
        letterSpacing: type.tracking.normal,
        cursor: clearing || allRecords.length === 0 ? "not-allowed" : "pointer",
        opacity: allRecords.length === 0 ? 0.4 : 1,
      }}
      >
      {clearing ? "CLEARING…" : "CLEAR"}
      </button>
      </div>

      {/* Log output */}
      <div
      ref={scrollRef}
      onScroll={handleScroll}
      style={{
        height: 380,
        overflowY: "auto",
        background: surface.logBg,
        border: `1px solid ${palette.border}`,
        borderRadius: radius.sm,
        padding: `${space.md}px 0`,
        fontFamily: type.mono,
        fontSize: type.size.md,
        lineHeight: type.leading.loose,
      }}
      >
      {records.length === 0 ? (
        <div style={{ color: palette.dim, padding: `${space.sm}px ${space.xl}px`, fontSize: type.size.md }}>
        {allRecords.length === 0
          ? "No log records yet — records appear here as the application logs events."
          : `No ${levelFilter} or higher records in the buffer.`
        }
        </div>
      ) : (
        records.map((r, i) => {
          const lvlColor = levelColor[r.level] || palette.muted;
          return (
            <div
            key={i}
            style={{
              display: "flex",
              gap: 0,
              padding: `0 ${space.xl}px`,
              background: i % 2 === 0 ? "transparent" : surface.zebraBg,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
            >
            <span style={{ color: surface.logMeta, flexShrink: 0, marginRight: space.sm }}>
            {fmtClock(r.ts)}
            </span>
            <span style={{
              color: lvlColor,
              flexShrink: 0,
              marginRight: space.sm,
              minWidth: 60,
              // Read off LEVEL_ORDER rather than naming levels: written as
              // `ERROR || WARNING` this left CRITICAL at normal weight, so
              // the most severe line rendered less prominently than an ERROR
              // directly above it, in the same red. An unknown level has no
              // rank and stays normal, as before.
              fontWeight: LEVEL_ORDER[r.level] >= LEVEL_ORDER.WARNING
              ? type.weight.bold : type.weight.normal,
            }}>
            {r.level}
            </span>
            <span style={{ color: surface.logMeta, flexShrink: 0, marginRight: space.sm }}>
            {r.module}
            </span>
            <span style={{ color: surface.logText }}>
            {r.message}
            </span>
            </div>
          );
        })
      )}
      </div>
      </div>
    );
};
