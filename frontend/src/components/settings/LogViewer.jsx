import { useState, useEffect, useRef } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";

/* ═══════════════════════════════════════════════════════════════════════════
 * LOG VIEWER
 * Polls GET /api/logs every 3 seconds while mounted.  Mounted only when
 * the user navigates to Settings, so polling stops automatically on
 * navigation away.
 *
 * Level filter is client-side — all 200 most recent records are fetched
 * and the selected minimum level is applied locally, so toggling is instant
 * without a new network round-trip.
 *
 * Auto-scroll keeps the list pinned to the newest entry.  It disengages
 * automatically when the user scrolls up, and re-engages when they scroll
 * back to the bottom.
 ═ ═*═════════════════════════════════════════════════════════════════════════ */

const LEVEL_ORDER = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 3 };
const LEVELS = ["ALL", "INFO", "WARNING", "ERROR"];

export const LogViewer = ({ api }) => {
  const { palette, type, space, legacy, levelColor } = useTheme();
  const [allRecords,  setAllRecords]  = useState([]);
  const [levelFilter, setLevelFilter] = useState("INFO");
  const [autoScroll,  setAutoScroll]  = useState(true);
  const [clearing,    setClearing]    = useState(false);

  const scrollRef    = useRef(null);
  const atBottomRef  = useRef(true);  // tracks whether user is at the bottom

  // ── Polling ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const poll = () => {
      fetch(`${api}/api/logs/?limit=200`)
      .then(r => r.json())
      .then(d => setAllRecords(d.records || []))
      .catch(() => {});
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
      try {
        await fetch(`${api}/api/logs/`, { method: "DELETE" });
        setAllRecords([]);
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
        const color  = l === "ALL" ? palette.muted : l === "INFO" ? palette.muted : l === "WARNING" ? palette.amber : palette.red;
        return (
          <button
          key={l}
          onClick={() => setLevelFilter(l)}
          style={{
            padding: `${space.xxs}px ${space.md}px`,
            background: active ? `${alpha(color, ALPHA.medium)}` : "transparent",
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
      onClick={() => setAutoScroll(a => !a)}
      title="Toggle auto-scroll to newest entry"
      style={{
        padding: `${space.xxs}px ${space.md}px`,
        background: autoScroll ? `${alpha(palette.blue, ALPHA.medium)}` : "transparent",
            border: `1px solid ${autoScroll ? palette.blue : palette.border}`,
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
        background: legacy.logBg,
        border: `1px solid ${palette.border}`,
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
              background: i % 2 === 0 ? "transparent" : legacy.zebraBg,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
            >
            <span style={{ color: legacy.logMeta, flexShrink: 0, marginRight: space.sm }}>
            {r.ts}
            </span>
            <span style={{
              color: lvlColor,
              flexShrink: 0,
              marginRight: space.sm,
              minWidth: 60,
              fontWeight: r.level === "ERROR" || r.level === "WARNING"
              ? type.weight.bold : type.weight.normal,
            }}>
            {r.level}
            </span>
            <span style={{ color: legacy.logMeta, flexShrink: 0, marginRight: space.sm }}>
            {r.module}
            </span>
            <span style={{ color: legacy.logText }}>
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
