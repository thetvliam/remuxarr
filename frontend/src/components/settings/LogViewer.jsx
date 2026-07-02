import { useState, useEffect, useRef } from "react";
import { C } from "../../constants";

/* ═══════════════════════════════════════════════════════════════════════════
   LOG VIEWER
   Polls GET /api/logs every 3 seconds while mounted.  Mounted only when
   the user navigates to Settings, so polling stops automatically on
   navigation away.

   Level filter is client-side — all 200 most recent records are fetched
   and the selected minimum level is applied locally, so toggling is instant
   without a new network round-trip.

   Auto-scroll keeps the list pinned to the newest entry.  It disengages
   automatically when the user scrolls up, and re-engages when they scroll
   back to the bottom.
═══════════════════════════════════════════════════════════════════════════ */

const LEVEL_ORDER = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 3 };
const LEVEL_COLOR = {
  DEBUG:    C.dim,
  INFO:     C.muted,
  WARNING:  C.amber,
  ERROR:    C.red,
  CRITICAL: C.red,
};

const LEVELS = ["ALL", "INFO", "WARNING", "ERROR"];

export const LogViewer = ({ api }) => {
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
    <div style={{ marginTop: 36, paddingTop: 24, borderTop: `1px solid ${C.border}` }}>
      {/* Section header */}
      <div style={{
        color: C.amber,
        fontSize: 9,
        letterSpacing: "0.18em",
        fontWeight: 700,
        marginBottom: 14,
      }}>
        APPLICATION LOGS
      </div>

      {/* Controls */}
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        marginBottom: 8,
        flexWrap: "wrap",
      }}>
        {/* Level filter */}
        <div style={{ display: "flex" }}>
          {LEVELS.map((l, i) => {
            const active = levelFilter === l;
            const color  = l === "ALL" ? C.muted : l === "INFO" ? C.muted : l === "WARNING" ? C.amber : C.red;
            return (
              <button
                key={l}
                onClick={() => setLevelFilter(l)}
                style={{
                  padding: "3px 10px",
                  background: active ? `${color}22` : "transparent",
                  border: `1px solid ${active ? color : C.border}`,
                  borderRight: i < LEVELS.length - 1 ? "none" : undefined,
                  color: active ? color : C.dim,
                  fontSize: 9,
                  fontFamily: "inherit",
                  letterSpacing: "0.08em",
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
            padding: "3px 10px",
            background: autoScroll ? `${C.blue}22` : "transparent",
            border: `1px solid ${autoScroll ? C.blue : C.border}`,
            color: autoScroll ? C.blue : C.dim,
            fontSize: 9,
            fontFamily: "inherit",
            letterSpacing: "0.08em",
            cursor: "pointer",
          }}
        >
          ↓ AUTO-SCROLL
        </button>

        {/* Record count */}
        <span style={{ color: C.dim, fontSize: 10, marginLeft: 4 }}>
          {records.length} record{records.length === 1 ? "" : "s"}
        </span>

        {/* Clear */}
        <button
          onClick={clearLogs}
          disabled={clearing || allRecords.length === 0}
          style={{
            marginLeft: "auto",
            padding: "3px 10px",
            background: "transparent",
            border: `1px solid ${C.border}`,
            color: C.dim,
            fontSize: 9,
            fontFamily: "inherit",
            letterSpacing: "0.08em",
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
          background: "#0d0f1a",
          border: `1px solid ${C.border}`,
          padding: "10px 0",
          fontFamily: "'Courier New', 'Lucida Console', monospace",
          fontSize: 11,
          lineHeight: 1.7,
        }}
      >
        {records.length === 0 ? (
          <div style={{ color: C.dim, padding: "8px 14px", fontSize: 11 }}>
            {allRecords.length === 0
              ? "No log records yet — records appear here as the application logs events."
              : `No ${levelFilter} or higher records in the buffer.`
            }
          </div>
        ) : (
          records.map((r, i) => {
            const lvlColor = LEVEL_COLOR[r.level] || C.muted;
            return (
              <div
                key={i}
                style={{
                  display: "flex",
                  gap: 0,
                  padding: "0 14px",
                  background: i % 2 === 0 ? "transparent" : "#ffffff04",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                }}
              >
                <span style={{ color: "#3a4060", flexShrink: 0, marginRight: 8 }}>
                  {r.ts}
                </span>
                <span style={{
                  color: lvlColor,
                  flexShrink: 0,
                  marginRight: 8,
                  minWidth: 60,
                  fontWeight: r.level === "ERROR" || r.level === "WARNING" ? 700 : 400,
                }}>
                  {r.level}
                </span>
                <span style={{ color: "#3a4060", flexShrink: 0, marginRight: 8 }}>
                  {r.module}
                </span>
                <span style={{ color: "#c8cce8" }}>
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
