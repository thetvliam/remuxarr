import { useState, useEffect, useRef } from "react";

/* ═══════════════════════════════════════════════════════════════════════════
 *  WEBSOCKET HOOK
 *  Uses a callback ref so the effect never re-fires on re-renders;
 *  only reconnects when the URL changes.
 ═ *══════════════════════════════════════════════════════════════════════════ */
export function useWebSocket(url, onMessage, onReconnect) {
    const wsRef    = useRef(null);
    const cbRef    = useRef(onMessage);
    const rcRef    = useRef(onReconnect);
    const timerRef = useRef(null);
    const [connected, setConnected] = useState(false);

    // Keep callback refs fresh without triggering reconnects
    useEffect(() => { cbRef.current = onMessage; });
    useEffect(() => { rcRef.current = onReconnect; });

    useEffect(() => {
        let active = true;
        // DELIBERATE DESIGN — DO NOT convert to useState or move outside this
        // effect. `isFirstConnect` must be a plain closure variable so it
        // survives across the effect's lifetime without triggering re-renders.
        // It exists to suppress the onReconnect callback on the very first
        // WebSocket connection (page load) — only actual *reconnects* (e.g.
        // after a backend restart) should trigger a full data refresh. If this
        // guard is removed or converted to React state, fetchAll() will fire
        // twice on every page load, racing the mount-time fetchAll() call.
        let isFirstConnect = true;

        function connect() {
            if (!active) return;
            try {
                const ws = new WebSocket(url);
                wsRef.current = ws;

                ws.onopen = () => {
                    if (!active) return;
                    setConnected(true);
                    // On reconnect (not the very first connection), re-fetch all
                    // state so the dashboard reflects whatever changed while the
                    // WebSocket was down (e.g. jobs that finished or were reset
                    // during a container restart showing as stuck at 100%).
                    if (!isFirstConnect) {
                        rcRef.current?.();
                    }
                    isFirstConnect = false;
                };
                ws.onmessage = (e) => {
                    if (e.data === "pong") return;
                    try { cbRef.current(JSON.parse(e.data)); } catch (_) {}
                };
                ws.onclose = () => {
                    setConnected(false);
                    timerRef.current = setTimeout(connect, 3000);
                };
                ws.onerror = () => ws.close();
            } catch (_) {
                timerRef.current = setTimeout(connect, 3000);
            }
        }

        connect();

        // Keepalive ping every 25 s
        const ping = setInterval(() => {
            if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send("ping");
        }, 25000);

            return () => {
                active = false;
                clearTimeout(timerRef.current);
                clearInterval(ping);
                wsRef.current?.close();
            };
    }, [url]);

    return connected;
}
