import { useEffect, useState } from "react";
import { useTheme } from "../../theme";

/* ── Build info ──────────────────────────────────────────────────────────────
 * Which build is running. Lives under Maintenance & Logs rather than in an
 * About screen because the bug report template asks for the version and the
 * logs together, and someone filing one should not have to visit two places
 * to answer it.
 *
 * The version is the git ref the image was built from — a release tag, or a
 * branch name for images built from main/testing. The commit is what actually
 * identifies the build; the ref is there to make it readable. A source
 * checkout reports "dev"/"unknown" because nothing stamped it, which is the
 * honest answer rather than a version number that was never true.
 *
 * Copy writes the full SHA, not the seven shown. The short form is for
 * reading; a bug report wants the value that can be checked out without
 * ambiguity. Falls back to selectable text when the clipboard API is absent,
 * which is the case over plain HTTP on a LAN address — exactly how most
 * people reach this app, so the fallback is the common path and not an
 * edge case. ─────────────────────────────────────────────────────────────── */

export const BuildInfoSection = ({ api }) => {
  const { palette, type, space } = useTheme();

  const [build, setBuild] = useState(null);
  const [failed, setFailed] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const r = await fetch(`${api}/api/health`);
        if (!r.ok) throw new Error(String(r.status));
        const d = await r.json();
        if (live) setBuild(d);
      } catch (_) {
        if (live) setFailed(true);
      }
    })();
    return () => { live = false; };
  }, [api]);

  /* Keyed on the flag with cleanup rather than a bare setTimeout in the
   * handler, matching SettingsPage's save status and the four confirm
   * buttons — this was the last bare one left. Two effects of that: one
   * timer exists at a time instead of one per click, and it is cleared on
   * unmount, which here is a single click away since the section unmounts on
   * every Settings category switch.
   *
   * It does NOT restart the window when COPIED is already showing — the flag
   * does not change, so the effect does not re-run, and a second copy at
   * 1.9s still clears at 2.0s. Checked rather than assumed. Left that way on
   * purpose: it matches the other four timers, and the cost of the edge is a
   * label clearing early, not a wrong result. */
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(t);
  }, [copied]);

  if (failed || !build) return null;

  const label = `${build.version} (${build.commit_short})`;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${build.version} ${build.commit}`);
      setCopied(true);
    } catch (_) {
      /* No clipboard: the text beside the button is selectable, which is
       * why the value is rendered rather than living only in the handler. */
    }
  };

  const canCopy = typeof navigator !== "undefined"
    && navigator.clipboard
    && typeof navigator.clipboard.writeText === "function";

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: space.sm,
      padding: `${space.lg}px 0 0`,
      color: palette.muted,
      fontSize: type.size.sm,
      fontFamily: type.family,
    }}>
    <span>Build</span>
    <code
    title={build.commit}
    style={{ fontFamily: type.mono, color: palette.text }}
    >
    {label}
    </code>
    {canCopy && (
      <button
      type="button"
      onClick={copy}
      style={{
        background: "transparent",
        border: `1px solid ${palette.muted}`,
        color: palette.muted,
        fontSize: type.size.xs,
        fontFamily: type.family,
        padding: `${space.hair}px ${space.sm}px`,
        cursor: "pointer",
      }}
      >
      {copied ? "COPIED" : "COPY"}
      </button>
    )}
    </div>
  );
};
