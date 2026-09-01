import { useCallback, useEffect, useState } from "react";
import { useTheme, alpha, ALPHA } from "../../theme";
import { fmtSize, fmtRel, basename } from "../../utils";
import { CONFIRM_MS } from "../../constants";
import { Btn } from "../atoms/Btn";
import { EmptyState } from "../atoms/EmptyState";

/* revert_match.COMPATIBLE. A compatible refusal is "plausible but I could
 * not verify it", which is a different thing to say to the user than
 * "this is not the same content" — amber rather than red. */
const COMPATIBLE_TIER = "compatible";

/* ── Recycle Bin ─────────────────────────────────────────────────────────────
 * Lives in Settings rather than the header nav on purpose. This is a safety
 * net for the period while someone is still working out what their language
 * and subtitle rules should be, not a place to visit regularly — and putting
 * it beside the settings that fill it means the retention limits are right
 * there when the size prompts a question.
 *
 * Two lists, because they support different actions and a single list with a
 * status column invites offering Revert on something that has no file to
 * revert:
 *
 *   ATTACHED   knows its file, can be reverted.
 *   UNMATCHED  lost its file — almost always a rename, since the scanner
 *              cannot tell one from a deletion. Must be matched first.
 *
 * Every destructive action takes two clicks with a 4s timeout, matching
 * DangerZone. Reverting overwrites a media file and discarding throws away
 * the only copy of the removed tracks; neither should be one stray click
 * away. ────────────────────────────────────────────────────────────────── */


export const RecycleBinSection = ({ api, toast, reloadKey }) => {
  const { palette, type, space } = useTheme();

  const [data,    setData]    = useState(null);
  const [error,   setError]   = useState(false);
  const [busy,    setBusy]    = useState(false);
  const [matching, setMatching] = useState(null);   // point being matched
  const [running, setRunning] = useState(null);     // revert in flight

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${api}/api/revert/`);
      if (!r.ok) throw new Error(String(r.status));
      setData(await r.json());
      setError(false);
    } catch (err) {
      console.error("Failed to load revert points", err);
      setError(true);
    }
    // One revert runs at a time server-side, and the POST returns as soon
    // as it has STARTED. Asking who is running means a reload — or opening
    // Settings mid-revert, or coming back after a page refresh — shows the
    // truth rather than offering buttons that will be refused.
    try {
      const r = await fetch(`${api}/api/revert/status`);
      if (r.ok) {
        const body = await r.json();
        setRunning(body.running ? body.point_id : null);
      }
    } catch (err) {
      console.error("Failed to read revert status", err);
    }
  }, [api]);

  useEffect(() => { load(); }, [load, reloadKey]);

  const act = async (path, options, okMsg, onRefusal) => {
    setBusy(true);
    try {
      const r = await fetch(`${api}${path}`, options);
      const body = await r.json().catch(() => ({}));
      if (r.ok) {
        toast?.(okMsg, "success");
      } else {
        // The backend's refusals carry the reason — an unmatched file, a
        // changed fingerprint, a queued job. Replacing them with a generic
        // failure is what turns "this file was upgraded since" into "revert
        // failed", which tells the user nothing they can act on.
        const detail = body?.detail;
        // Some refusals carry more than one line — attach() reports every
        // stream that is missing, with its codec and language, and how far
        // the runtimes diverge. A toast is one line and disappears, so a
        // caller that can show the whole thing next to the choice it
        // relates to takes it instead. Only structured refusals qualify:
        // a plain-string detail has nothing more to render.
        if (onRefusal && detail && typeof detail === "object") {
          onRefusal(detail);
        } else {
          toast?.(typeof detail === "string" ? detail
          : detail?.error || "Request failed", "error");
        }
      }
      return r.ok;
    } catch (err) {
      console.error("Recycle bin request failed", err);
      toast?.("Request failed", "error");
      return false;
    } finally {
      setBusy(false);
      load();
    }
  };

  if (error) {
    return (
      <Panel title="RECYCLE BIN · BETA">
      <Note tone="red">
      Couldn&apos;t load the recycle bin. Reload the page to try again.
      </Note>
      </Panel>
    );
  }

  if (!data) {
    return (
      <Panel title="RECYCLE BIN · BETA">
      <Note tone="dim">Loading…</Note>
      </Panel>
    );
  }

  const { attached = [], detached = [] } = data;
  const totalBytes = [...attached, ...detached]
  .reduce((sum, p) => sum + (p.sidecar_size || 0), 0);

  return (
    <Panel title="RECYCLE BIN · BETA">
    {/* An empty bin means two different things and the difference matters:
      * nothing kept yet, or the volume was never mounted. */}
      {!data.recycle_bin_ready && (
        <Note tone="amber">
        {data.recycle_bin_reason || "The recycle bin is not available."}
        </Note>
      )}

      {data.recycle_bin_ready && (
        <div style={{
          display: "flex", alignItems: "center", gap: space.md,
          color: palette.muted, fontSize: type.size.sm,
          marginBottom: space.lg,
        }}>
        <span>{attached.length + detached.length} stored</span>
        <span style={{ color: palette.dim }}>·</span>
        <span>{fmtSize(totalBytes)}</span>
        </div>
      )}

      <SubHeading label="RESTORABLE" count={attached.length} />
      {attached.length === 0 ? (
        <EmptyState msg="Nothing stored yet — process a file with the recycle bin on and its removed tracks are kept here" />
      ) : (
        attached.map(point => (
          <AttachedRow
          key={point.id}
          point={point}
          busy={busy}
          running={running === point.id}
          blocked={running !== null && running !== point.id}
          onRevert={async () => {
            // Held locally as well as read back from the server: the reload
            // inside act() races the revert it just started, and without
            // this the row briefly offers REVERT again on a file that is
            // already being rewritten.
            setRunning(point.id);
            const ok = await act(`/api/revert/${point.id}/restore/`,
                                 { method: "POST" }, "Revert started");
            if (!ok) setRunning(null);
          }}
          onDiscard={() => act(`/api/revert/${point.id}/`, { method: "DELETE" },
                               "Discarded")}
                               />
        ))
      )}

      {detached.length > 0 && (
        <>
        <SubHeading label="UNMATCHED" count={detached.length} />
        <Note tone="dim">
        These lost track of their file — usually because it was renamed or
        moved. Match one to a file to make it restorable again.
        </Note>
        {detached.map(point => (
          <DetachedRow
          key={point.id}
          point={point}
          busy={busy}
          onMatch={() => setMatching(point)}
          onDiscard={() => act(`/api/revert/${point.id}/`, { method: "DELETE" },
                               "Discarded")}
                               />
        ))}
        </>
      )}

      {matching && (
        <MatchPanel
        api={api}
        point={matching}
        onClose={() => setMatching(null)}
        onAttach={async (fileId, confirmMismatch) => {
          // The refusal goes back to the panel rather than to a toast. The
          // user is picking from a list here, so "no" on its own just sends
          // them to try the next candidate at random — which is the outcome
          // the backend builds these reasons to prevent.
          let refusal = null;
          const ok = await act(
            `/api/revert/${matching.id}/attach/`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ file_id: fileId, confirm_mismatch: confirmMismatch }),
            },
            "Matched — this file can now be reverted",
            detail => { refusal = detail; },
          );
          if (ok) setMatching(null);
          return { ok, refusal };
        }}
        />
      )}

      {(attached.length > 0 || detached.length > 0) && (
        <div style={{ display: "flex", gap: space.md, marginTop: space.xl }}>
        {detached.length > 0 && (
          <ConfirmBtn
          label="DISCARD UNMATCHED"
          confirmLabel="CONFIRM — DISCARD UNMATCHED"
          color={palette.amber}
          /* Both bulk buttons delete sidecars, including the one a running
           * restore is reading from, so both are held while a revert is in
           * flight — the same reason the per-row DISCARD already is. This
           * one looks safe because restore() only accepts an attached point
           * and this sweep takes only detached ones, but a rescan can detach
           * a point while its revert is still running. */
          disabled={busy || running !== null}
          onConfirm={() => act("/api/revert/?detached_only=true", { method: "DELETE" },
                               "Unmatched entries discarded")}
                               />
        )}
        <ConfirmBtn
        label="EMPTY RECYCLE BIN"
        confirmLabel="CONFIRM — EMPTY EVERYTHING"
        color={palette.red}
        disabled={busy || running !== null}
        onConfirm={() => act("/api/revert/", { method: "DELETE" },
                             "Recycle bin emptied")}
                             />
                             </div>
      )}
      </Panel>
  );
};

/* ── Rows ─────────────────────────────────────────────────────────────────── */

const AttachedRow = ({ point, busy, running, blocked, onRevert, onDiscard }) => {
  const { palette, type, space } = useTheme();
  const movedTo = point.original_path && point.current_path
  && point.original_path !== point.current_path;

  return (
    <Row>
    <div style={{ minWidth: 0, flex: 1 }}>
    <div style={{ color: palette.text, fontSize: type.size.sm, overflowWrap: "anywhere" }}>
    {point.current_filename || basename(point.original_path)}
    </div>
    <div style={{ color: palette.dim, fontSize: type.size.xs, marginTop: space.hair }}>
    {fmtSize(point.sidecar_size)} · kept {fmtRel(point.created_at)}
    {/* A container conversion renames the file, so the name on disk is
      * not the one that will come back. Saying so prevents a surprise. */}
      {movedTo && <> · restores as {basename(point.original_path)}</>}
      </div>
      {/* The entry stays listed even when it cannot be used — the stored
        * tracks are still on the volume and still taking up space, so it
        * has to be visible to be discarded. What it must not do is offer
        * Revert: that produces a refusal the user has no way to explain. */}
        {point.restorable === false && point.blocked_reason && (
          <div style={{ color: palette.amber, fontSize: type.size.xs,
            marginTop: space.hair }}>
            {point.blocked_reason}
            </div>
        )}
        </div>
        <div style={{ display: "flex", gap: space.sm, flexShrink: 0 }}>
        {/* Only one revert runs at a time, so a second click is refused by
          * the API. Showing that state beats letting someone click and get an
          * error toast they may not see — which reads as the button doing
          * nothing at all. */}
          {running ? (
            <Btn label="REVERTING…" color={palette.cyan} disabled onClick={() => {}} />
          ) : point.restorable === false ? null : (
            <ConfirmBtn label="REVERT" confirmLabel="CONFIRM REVERT"
            color={palette.amber} disabled={busy || blocked} onConfirm={onRevert} />
          )}
          <ConfirmBtn label="DISCARD" confirmLabel="CONFIRM"
          color={palette.red} disabled={busy || running} onConfirm={onDiscard} />
          </div>
          </Row>
  );
};

const DetachedRow = ({ point, busy, onMatch, onDiscard }) => {
  const { palette, type, space } = useTheme();
  const tracks = point.stored_tracks || [];
  const languages = [...new Set(tracks.map(t => t.language).filter(Boolean))];

  return (
    <Row>
    <div style={{ minWidth: 0, flex: 1 }}>
    <div style={{ color: palette.text, fontSize: type.size.sm, overflowWrap: "anywhere" }}>
    {point.original_filename || basename(point.original_path)}
    </div>
    <div style={{ color: palette.dim, fontSize: type.size.xs, marginTop: space.hair }}>
    {fmtSize(point.sidecar_size)} · {tracks.length} track{tracks.length === 1 ? "" : "s"}
    {languages.length > 0 && <> ({languages.join(", ")})</>}
    {point.detached_at && <> · unmatched {fmtRel(point.detached_at)}</>}
    </div>
    {/* Retention may have taken the sidecar while the row survived. Such
      * an entry can never restore anything, so offering Match would spend
      * the user's attention on a dead end. */}
      {!point.sidecar_present && (
        <div style={{ color: palette.red, fontSize: type.size.xs, marginTop: space.hair }}>
        Stored tracks are missing from the recycle volume — this can no longer restore anything.
        </div>
      )}
      </div>
      <div style={{ display: "flex", gap: space.sm, flexShrink: 0 }}>
      {point.sidecar_present && (
        <Btn label="MATCH" color={palette.cyan} onClick={onMatch} disabled={busy} />
      )}
      <ConfirmBtn label="DISCARD" confirmLabel="CONFIRM"
      color={palette.red} disabled={busy} onConfirm={onDiscard} />
      </div>
      </Row>
  );
};

/* ── Matching ─────────────────────────────────────────────────────────────── */

const MatchPanel = ({ api, point, onClose, onAttach }) => {
  const { palette, type, space, radius } = useTheme();
  const [candidates, setCandidates] = useState(null);
  const [failed, setFailed] = useState(false);
  const [refusal, setRefusal] = useState(null);

  /* Attach, and keep any refusal here rather than letting it become a
   * toast. assess() reports which streams are missing with their codecs
   * and languages, or how far the runtimes diverge — the detail that
   * tells the user whether to try another candidate or stop looking.
   * Cleared on each attempt so an old refusal never sits under a new one. */
  const pick = async (fileId, confirmMismatch) => {
    setRefusal(null);
    const result = await onAttach(fileId, confirmMismatch);
    if (result?.refusal) setRefusal(result.refusal);
  };

    useEffect(() => {
      let cancelled = false;
      (async () => {
        try {
          const r = await fetch(`${api}/api/revert/${point.id}/candidates/`);
          if (!r.ok) throw new Error(String(r.status));
          const body = await r.json();
          if (!cancelled) setCandidates(body);
        } catch (err) {
          console.error("Failed to load candidates", err);
          if (!cancelled) setFailed(true);
        }
      })();
      return () => { cancelled = true; };
    }, [api, point.id]);

    const exact  = candidates?.exact  || [];
    const nearby = candidates?.nearby || [];

    return (
      <div style={{
        border: `1px solid ${palette.cyan}`,
        borderRadius: radius.md,
        padding: space.lg,
        marginTop: space.lg,
        background: alpha(palette.cyan, ALPHA.faint),
      }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: space.md }}>
      <span style={{ color: palette.cyan, fontSize: type.size.xs,
        letterSpacing: type.tracking.widest, fontWeight: type.weight.bold }}>
        MATCH {basename(point.original_path)}
        </span>
        <div style={{ marginLeft: "auto" }}>
        <Btn label="CANCEL" color={palette.dim} onClick={onClose} />
        </div>
        </div>

        {failed && <Note tone="red">Couldn&apos;t load candidates.</Note>}

        {refusal && (
          <Note tone={refusal.tier === COMPATIBLE_TIER ? "amber" : "red"}>
          <div style={{ fontWeight: type.weight.bold }}>
          {refusal.error || "That file was not accepted."}
          </div>
          {/* Every reason, not the first. A file can be rejected for
            * several streams at once, and knowing it is one missing
            * commentary track rather than a different episode entirely is
            * the difference between picking another candidate and giving
            * up on the list. */}
            {(refusal.reasons || []).map((reason, i) => (
              <div key={i} style={{ marginTop: space.xs }}>{reason}</div>
            ))}
            </Note>
        )}
        {!candidates && !failed && <Note tone="dim">Looking for matches…</Note>}

        {exact.length > 0 && (
          <>
          <Note tone="green">
          {/* Worth stating plainly: this is not a guess. A rename does not
            * change a byte, so a file still carrying the recorded size and
            * timestamp IS this one. */}
            Same size and timestamp as the file this was taken from — a renamed
            copy of the same file.
            </Note>
            {exact.map(file => (
              <CandidateRow key={file.id} file={file}
              label="USE THIS FILE" color={palette.green}
              onPick={() => pick(file.id, false)} />
            ))}
            </>
        )}

        {nearby.length > 0 && (
          <>
          <Note tone="dim">
          Other files where the original lived. These can&apos;t be verified,
          so reverting one afterwards could put the wrong tracks into it —
          only pick one you recognise.
          </Note>
          {nearby.map(file => (
            <CandidateRow key={file.id} file={file}
            label="USE ANYWAY" color={palette.amber}
            onPick={() => pick(file.id, true)} confirm />
          ))}
          </>
        )}

        {candidates && exact.length === 0 && nearby.length === 0 && (
          <Note tone="dim">
          No candidates found. The file may have moved to another library.
          </Note>
        )}
        </div>
    );
};

const CandidateRow = ({ file, label, color, onPick, confirm }) => {
  const { palette, type } = useTheme();

  return (
    <Row>
    <div style={{ minWidth: 0, flex: 1 }}>
    <div style={{ color: palette.text, fontSize: type.size.sm, overflowWrap: "anywhere" }}>
    {file.filename}
    </div>
    <div style={{ color: palette.dim, fontSize: type.size.xs }}>{file.path}</div>
    </div>
    {confirm
      ? <ConfirmBtn label={label} confirmLabel="CONFIRM — UNVERIFIED"
      color={color} onConfirm={onPick} />
      : <Btn label={label} color={color} onClick={onPick} />}
      </Row>
  );
};

/* ── Shared bits ──────────────────────────────────────────────────────────── */

const Panel = ({ title, children }) => {
  const { palette, type, space, radius } = useTheme();
  return (
    <div style={{
      border: `1px solid ${palette.border}`,
      borderRadius: radius.md,
      padding: space.xl,
      marginBottom: space.xl,
      background: palette.card,
    }}>
    <div style={{
      color: palette.muted, fontSize: type.size.xs,
      letterSpacing: type.tracking.max, fontWeight: type.weight.bold,
      marginBottom: space.lg,
    }}>
    {title}
    </div>
    {children}
    </div>
  );
};

const SubHeading = ({ label, count }) => {
  const { palette, type, space } = useTheme();
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: space.sm,
      marginTop: space.lg, marginBottom: space.sm,
      color: palette.dim, fontSize: type.size.xs,
      letterSpacing: type.tracking.widest, fontWeight: type.weight.bold,
    }}>
    <span>{label}</span>
    <span style={{ color: palette.muted }}>{count}</span>
    </div>
  );
};

const Row = ({ children }) => {
  const { palette, space } = useTheme();
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: space.md,
      padding: `${space.sm}px 0`,
      borderBottom: `1px solid ${palette.border}`,
    }}>
    {children}
    </div>
  );
};

const Note = ({ tone, children }) => {
  const { palette, type, space } = useTheme();
  const color = { red: palette.red, amber: palette.amber,
    green: palette.green, dim: palette.dim }[tone] || palette.dim;
    return (
      <div style={{
        color, fontSize: type.size.xs, lineHeight: 1.5,
        margin: `${space.sm}px 0`,
      }}>
      {children}
      </div>
    );
};

/* Two clicks with a 4s timeout, matching DangerZone. The cleanup on the
 * effect is what stops rapid clicks stacking timeouts that then reset the
 * confirmation at an unexpected moment. */
const ConfirmBtn = ({ label, confirmLabel, color, disabled, onConfirm }) => {
  const { palette } = useTheme();
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!confirming) return;
    const t = setTimeout(() => setConfirming(false), CONFIRM_MS);
    return () => clearTimeout(t);
  }, [confirming]);

  return (
    <Btn
    label={confirming ? confirmLabel : label}
    color={confirming ? palette.red : color}
    disabled={disabled}
    onClick={() => {
      if (!confirming) { setConfirming(true); return; }
      setConfirming(false);
      onConfirm();
    }}
    />
  );
};
