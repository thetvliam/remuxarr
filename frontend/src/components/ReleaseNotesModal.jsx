import { useEffect, useRef, useState } from "react";
import { useTheme, LAYER } from "../theme";
import { Btn } from "./atoms/Btn";

/* ═══════════════════════════════════════════════════════════════════════════
 * RELEASE NOTES
 *
 * Shown once, the first time the app loads after RELEASE_NOTES.md changes.
 * The backend returns a hash of the parsed notes as `version`; the hash the
 * user dismissed is kept in localStorage, and the dialog appears exactly
 * when the two differ.
 *
 * A hash, not a version number, because these notes are not versioned —
 * the file is emptied and refilled each cycle. A release number would have
 * to be bumped by hand, and the release that gets forgotten is the one that
 * renamed somebody's settings.
 *
 * localStorage rather than a server-side setting, matching the theme and
 * the settings-tab memory: this is a per-person "I have read this", not
 * configuration. Storing it server-side would mark it read for everyone
 * because the first person to open the app did.
 ═══════════════════════════════════════════════════════════════════════════ */

const STORAGE_KEY = "remuxarr.releaseNotesSeen";

export const ReleaseNotesModal = ({ api }) => {
  const { palette, type, space, radius, surface } = useTheme();
  const [notes, setNotes] = useState(null);
  const panelRef = useRef(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const r = await fetch(`${api}/api/release-notes/`);
        if (!r.ok) return;
        const body = await r.json();
        /* No version means nothing to announce — an empty file, which is
         * the normal state of a cycle with no user-visible changes yet. */
        if (!body?.version || !body.sections?.length) return;

        let seen = null;
        try { seen = localStorage.getItem(STORAGE_KEY); } catch (_) { /* ignore */ }
        if (seen === body.version) return;

        if (!cancelled) setNotes(body);
      } catch (_) {
        /* An unreachable backend is the dashboard's problem to report, not
         * this dialog's. Failing quietly here is right: nobody needs a
         * second error about release notes on top of the app being down. */
      }
    })();

    return () => { cancelled = true; };
  }, [api]);

  /* Labelled, focus moved in, Escape closes, and the page behind is
   * scroll-locked so a flick of the wheel does not scroll a list the dialog
   * is covering.
   *
   * Short of DetailModal and UnsavedChangesModal, which also trap Tab inside
   * the dialog and restore focus to the trigger on close. This one has no
   * trigger to restore to — it opens itself once per release — and its only
   * focusable child is the dismiss button, so Tab has nowhere to escape to
   * that matters. Worth knowing before copying this as the pattern. */
  useEffect(() => {
    if (!notes) return undefined;

    panelRef.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") dismiss(); };
    window.addEventListener("keydown", onKey);

    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notes]);

  const dismiss = () => {
    /* Recorded before the dialog closes, and wrapped: a browser with
     * storage disabled or full would otherwise throw here and leave the
     * dialog up with a dead button. Worst case without storage is that it
     * shows again next load, which beats being unable to close it. */
    try {
      if (notes?.version) localStorage.setItem(STORAGE_KEY, notes.version);
    } catch (_) { /* ignore */ }
    setNotes(null);
  };

  if (!notes) return null;

  return (
    <div
    style={{
      position: "fixed",
      inset: 0,
      /* surface.modalScrimBg, the same scrim DetailModal and the
       * unsaved-changes dialog use. */
      background: surface.modalScrimBg,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: space.lg,
      zIndex: LAYER.modal,
    }}
    onClick={dismiss}
    >
    <div
    ref={panelRef}
    role="dialog"
    aria-modal="true"
    aria-labelledby="release-notes-title"
    tabIndex={-1}
    onClick={(e) => e.stopPropagation()}
    style={{
      /* palette.card, matching the app's other dialogs, and OPAQUE.
       * This read surface.raised, which is not a key the theme defines —
       * so it evaluated to undefined, React omitted the property, and the
       * panel rendered with no background at all: the queue and history
       * text behind it showed straight through the notes. An undefined
       * theme key fails silently and looks deliberate, so it is worth
       * naming rather than just correcting. */
      background: palette.card,
      border: `1px solid ${palette.border}`,
      borderRadius: radius.md,
      padding: space.xxl,
      maxWidth: 620,
      width: "100%",
      maxHeight: "80vh",
      overflowY: "auto",
      outline: "none",
    }}
    >
    <div
    id="release-notes-title"
    style={{
      color: palette.amber,
      fontSize: type.size.sm,
      letterSpacing: type.tracking.ultra,
      fontWeight: type.weight.bold,
      marginBottom: space.lg,
    }}
    >
    WHAT&apos;S NEW
    </div>

    {notes.sections.map((section) => (
      <div key={section.title} style={{ marginBottom: space.xl }}>
      <div style={{
        color: palette.text,
        fontSize: type.size.base,
        fontWeight: type.weight.semibold,
        marginBottom: space.sm,
      }}>
      {section.title}
      </div>
      <ul style={{
        margin: 0,
        paddingLeft: space.xl,
        color: palette.muted,
        fontSize: type.size.md,
        lineHeight: type.leading.relaxed,
      }}>
      {section.items.map((item, i) => (
        <li key={i} style={{ marginBottom: space.xs }}>{item}</li>
      ))}
      </ul>
      </div>
    ))}

    <div style={{ display: "flex", justifyContent: "flex-end" }}>
    <Btn label="GOT IT" color={palette.cyan} onClick={dismiss} />
    </div>
    </div>
    </div>
  );
};
