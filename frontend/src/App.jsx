import { useState, useEffect, useRef } from "react";
import { useTheme, alpha, ALPHA, LAYER } from "./theme";
import { useAppData } from "./hooks/useAppData";
import { useActions } from "./hooks/useActions";
import { Toasts } from "./components/layout/Toasts";
import { AppHeader } from "./components/header/AppHeader";
import { SettingsPage } from "./components/settings/SettingsPage";
import { ReviewPage } from "./components/review/ReviewPage";
import { ForgePage } from "./components/forge/ForgePage";
import { ThemeEditorPage } from "./components/theme/ThemeEditorPage";
import { ActivePanel } from "./components/dashboard/ActivePanel";
import { QueuePanel } from "./components/dashboard/QueuePanel";
import { HistoryPanel } from "./components/dashboard/HistoryPanel";
import { DetailModal } from "./components/DetailModal";
import { ReleaseNotesModal } from "./components/ReleaseNotesModal";

/* ── Unsaved-changes navigation guard modal ─────────────────────────────── */
const UnsavedChangesModal = ({ onKeep, onDiscard }) => {
  const { palette, type, space, radius, surface } = useTheme();
  const panelRef = useRef(null);

  /* This is the app's only blocking dialog and had none of the semantics
   * of one — no role, no Escape, and no focus handling. Assistive
   * technology announced it as an anonymous div, and a keyboard user was
   * left with focus still on whatever they were editing behind it, able to
   * tab straight back into a form the dialog exists to stop them leaving.
   *
   * Escape maps to Keep Editing, not Discard: dismissing a dialog should
   * never be the destructive branch.
   *
   * onKeep is read through a ref and the effect runs ONCE. It used to
   * depend on [onKeep], which App passes as an inline arrow — a new
   * identity on every render, and App re-renders on every job_progress
   * message, which arrives several times a second while a job runs. The
   * effect therefore tore down and re-ran continuously, and since setup
   * focuses the first button while teardown restores focus to wherever it
   * was, a keyboard user who tabbed to DISCARD CHANGES was dragged back to
   * KEEP EDITING before they could press it. The focus handling added to
   * make this dialog keyboard-accessible was making it keyboard-unusable.
   *
   * A ref rather than asking App to useCallback, so the dialog stays
   * correct however a caller chooses to pass its handlers. */
  const onKeepRef = useRef(onKeep);
  useEffect(() => { onKeepRef.current = onKeep; });

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); onKeepRef.current(); return; }
      if (e.key !== "Tab") return;
      // Focus trap. Without it Tab walks out of the dialog and into the page
      // behind, which for a modal is the one thing it must not do.
      const focusable = panelRef.current?.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
      if (!focusable?.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", onKey);
    // Move focus in, and put it back where it came from on close, so
    // dismissing the dialog returns the user to what they were doing.
    const previouslyFocused = document.activeElement;
    panelRef.current?.querySelector("button")?.focus();
    return () => {
      window.removeEventListener("keydown", onKey);
      previouslyFocused?.focus?.();
    };
  }, []);

  return (
    <div
    onClick={onKeep}
    style={{
      // Above every other layer, including the mobile header (600), its
      // drawer (500) and the detail modal (1000). This dialog exists to
      // block navigation, so anything rendering over it would defeat it —
      // at z-index 100 the mobile header stayed tappable on top of the
      // backdrop, letting nav buttons be used while the guard was open.
      position: "fixed", inset: 0, zIndex: LAYER.guardModal,
      background: surface.guardScrimBg,
      display: "flex", alignItems: "center", justifyContent: "center", padding: space.xxl,
    }}
    >
    <div
    ref={panelRef}
    role="dialog"
    aria-modal="true"
    aria-labelledby="unsaved-changes-title"
    onClick={e => e.stopPropagation()}
    style={{
      width: "100%", maxWidth: 400,
      background: palette.card, border: `1px solid ${palette.border}`,
      borderRadius: radius.sm,
      padding: `${space.huge}px ${space.huge}px ${space.xxl}px`,
    }}
    >
    <div id="unsaved-changes-title" style={{ color: palette.amber, fontSize: type.size.sm, letterSpacing: type.tracking.ultra, fontWeight: type.weight.bold, marginBottom: space.md }}>
    UNSAVED CHANGES
    </div>
    <div style={{ color: palette.text, fontSize: type.size.lg, lineHeight: type.leading.normal, marginBottom: space.xxl }}>
    You have unsaved settings changes. Leave without saving? Your changes will be lost.
    </div>
    <div style={{ display: "flex", gap: space.md, justifyContent: "flex-end" }}>
    <button
    onClick={onKeep}
    style={{
      padding: `${space.sm}px ${space.xl}px`, background: "transparent",
      border: `1px solid ${palette.muted}`, color: palette.text,
      borderRadius: radius.sm,
      fontSize: type.size.sm, fontFamily: type.family, fontWeight: type.weight.bold, letterSpacing: type.tracking.normal, cursor: "pointer",
    }}
    >
    KEEP EDITING
    </button>
    <button
    onClick={onDiscard}
    style={{
      padding: `${space.sm}px ${space.xl}px`, background: alpha(palette.red, ALPHA.medium),
          border: `1px solid ${palette.red}`, color: palette.red,
          borderRadius: radius.sm,
          fontSize: type.size.sm, fontFamily: type.family, fontWeight: type.weight.bold, letterSpacing: type.tracking.normal, cursor: "pointer",
    }}
    >
    DISCARD CHANGES
    </button>
    </div>
    </div>
    </div>
  );
};

/* ═══════════════════════════════════════════════════════════════════════════
 *  ROOT APP
 * ═ *══════════════════════════════════════════════════════════════════════════ */
export default function App() {
  const { palette, type, space, size } = useTheme();
  const data = useAppData();
  const { isMobile } = data;
  const [queueTab, setQueueTab] = useState("queue"); // mobile only
  const {
    api, setApi, page, setPage,
    registerNavGuard, leaveGuarded,
    // `queue` is deliberately not taken: QueuePanel renders pendingQueue,
    // the same list with in-progress items filtered out. Destructuring the
    // raw one alongside it invited picking the wrong variable.
    activeJobs, review,
    modal, setModal,
    toasts,
    dryRun,
    scanning, scanProgress,
    showApiBar, setShowApiBar,
    workerPaused,
    autoStart,
    forgeActive, forgeProcessed, forgeRefreshKey,
      toast, fetchAll, refreshAllPanels,
      pendingQueue, wsConnected, historyRefreshKey, invalidateHistory,
      reviewRefreshKey,
      revertRefreshKey,
  } = data;

  const {
    toggleDryRun, togglePause, toggleAutoStart, triggerScan, cancelScan,
    openDetail, retryItem, dismissItem, retryAllFailed,
    dismissQueueItem, clearQueue, prioritizeItem,
    abortJob, clearDryRun,
    forgeAdd, forgeUndo,
  } = useActions(data);

  /* ── Unsaved-changes navigation guard ─────────────────────────────────────
   *  SettingsPage reports whether it has unsaved edits via onDirtyChange.
   *  Two ways out of a dirty Settings page, both guarded:
   *
   *  • Nav tab click → requestPage declines to route and opens the confirm
   *    modal instead.
   *  • Back, browser or Android → the guard registered below refuses it, and
   *    useAppData restores the entry it had already left.
   *
   *  Switching CATEGORIES inside Settings doesn't route (edits are kept in
   *  state), so it isn't guarded. Refresh and tab close are a document unload,
   *  which neither of these sees, and stay with the beforeunload handler
   *  inside SettingsPage.
   *
   *  pendingNav records which kind is waiting, because discarding resolves
   *  them differently: a tab click still has to be performed, whereas a Back
   *  has to be re-issued so the entry it was heading for is the one reached. */
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [pendingNav,    setPendingNav]    = useState(null);

  const wouldLoseEdits = (target) =>
  page === "settings" && settingsDirty && target !== "settings";

  const requestPage = (target) => {
    if (wouldLoseEdits(target)) {
      setPendingNav({ kind: "page", page: target });
    } else {
      setPage(target);
    }
  };

  /* Registered as an effect, not called inline: the predicate closes over
   * `page` and `settingsDirty`, so it has to be replaced whenever either
   * changes or Back would be answered using a stale reading of both. */
  useEffect(() => registerNavGuard((target) => {
    if (!wouldLoseEdits(target)) return false;
    setPendingNav({ kind: "back" });
    return true;
  }));

  const discardAndLeave = () => {
    const nav = pendingNav;
    setSettingsDirty(false);
    setPendingNav(null);
    if (nav?.kind === "page") setPage(nav.page);
    else if (nav?.kind === "back") leaveGuarded();
  };

    /* ── Render ───────────────────────────────────────────────────────────── */
    return (
      <div style={{
        /* dvh, not vh. vh is the viewport INCLUDING the mobile address bar,
         * so the bottom of the layout — and the toast stack anchored to it —
         * sat underneath the bar. DetailModal already used dvh for exactly
         * this reason and said so; the shell it sits in did not.
         *
         * Feature-detected rather than written as two declarations: this is a
         * JS object, not a CSS rule, so a duplicate key would just replace the
         * first and leave no fallback at all on a browser without dvh. */
        height:
        typeof CSS !== "undefined" && CSS.supports?.("height", "100dvh")
        ? "100dvh"
        : "100vh",
        display: "flex",
        flexDirection: "column",
        background: palette.bg,
        color: palette.text,
        fontFamily: type.root,
        fontSize: type.size.lg,
      }}>

      {/* ╔══════════════════════════════════════════════╗
        ║  HEADER                                      ║
        ╚══════════════════════════════════════════════╝ */}
        <AppHeader
        page={page} setPage={requestPage}
        reviewCount={review.length}
        api={api} setApi={setApi} showApiBar={showApiBar} setShowApiBar={setShowApiBar}
        dryRun={dryRun} onToggleDryRun={toggleDryRun}
        autoStart={autoStart} onToggleAutoStart={toggleAutoStart}
        workerPaused={workerPaused} onTogglePause={togglePause}
        scanning={scanning} scanProgress={scanProgress} onTriggerScan={triggerScan} onCancelScan={cancelScan}
        wsConnected={wsConnected}
        isMobile={isMobile}
        />

        {/* ╔══════════════════════════════════════════════╗
          ║  PAGES                                       ║
          ╚══════════════════════════════════════════════╝ */}

          {page === "dashboard" && (
            <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {/* Top strip — active worker */}
            {activeJobs.length === 0 ? (
              <ActivePanel
              job={null}
              isMobile={isMobile}
              transitioning={!workerPaused && pendingQueue.length > 0}
              />
            ) : (
              activeJobs.map(job => <ActivePanel key={job.id} job={job} isMobile={isMobile} onAbort={abortJob} />)
            )}

            {/* Bottom half — queue + history
              Desktop: side by side. Mobile: tab-switched. */}
              <div style={{
                flex: 1,
                display: "flex",
                flexDirection: "column",
                overflow: "hidden",
                borderTop: `1px solid ${palette.border}`,
              }}>
              {/* Mobile tab bar */}
              {isMobile && (
                <div style={{
                  display: "flex",
                  flexShrink: 0,
                  borderBottom: `1px solid ${palette.border}`,
                  background: palette.card,
                }}>
                {[["queue", "QUEUE"], ["history", "HISTORY"]].map(([k, l]) => (
                  <button
                  key={k}
                  onClick={() => setQueueTab(k)}
                  style={{
                    flex: 1,
                    padding: `${space.md}px 0`,
                    background: "transparent",
                    border: "none",
                    borderBottom: queueTab === k
                    ? `${size.accentThin}px solid ${palette.amber}` : `${size.accentThin}px solid transparent`,
                    color: queueTab === k ? palette.amber : palette.dim,
                    fontSize: type.size.xs,
                    fontFamily: type.family,
                    letterSpacing: type.tracking.widest,
                    fontWeight: type.weight.bold,
                    cursor: "pointer",
                  }}
                  >
                  {l}
                  </button>
                ))}
                </div>
              )}

              {/* Panel area */}
              <div style={{
                flex: 1,
                display: "flex",
                overflow: "hidden",
              }}>
              {/* Queue panel — always shown desktop; shown on mobile when queueTab=queue */}
              {(!isMobile || queueTab === "queue") && (
                <div style={{
                  flex: 1,
                  borderRight: !isMobile ? `1px solid ${palette.border}` : "none",
                  overflow: "hidden",
                  display: "flex",
                  flexDirection: "column",
                }}>
                <QueuePanel
                items={pendingQueue}
                onSelect={item => openDetail(item, "/api/queue")}
                onDismiss={dismissQueueItem}
                onClear={clearQueue}
                onPrioritize={prioritizeItem}
                />
                </div>
              )}

              {/* History panel — always shown desktop; shown on mobile when queueTab=history */}
              {(!isMobile || queueTab === "history") && (
                <div style={{
                  flex: 1,
                  overflow: "hidden",
                  display: "flex",
                  flexDirection: "column",
                }}>
                <HistoryPanel
                api={api}
                historyRefreshKey={historyRefreshKey}
                onSelect={item => openDetail(item, "/api/history")}
                onRetryAll={retryAllFailed}
                onClearDryRun={clearDryRun}
                />
                </div>
              )}
              </div>
              </div>
              </div>
          )}

          {page === "settings" && (
            <div style={{ flex: 1, overflowY: "auto" }}>
            <SettingsPage
            api={api}
            toast={toast}
            isMobile={isMobile}
            revertRefreshKey={revertRefreshKey}
            onDirtyChange={setSettingsDirty}
            /* Clearing the database empties the queue, history, forge and
             *              revert tables. The endpoint broadcasts nothing, so this is the
             *              only thing that tells those panels. */
            onDatabaseCleared={refreshAllPanels}
            /* dry_run_mode and auto_start_jobs are rendered from the app-level
             *              state rather than the page's own loaded snapshot, and applied on
             *              click. The header owns them: it toggles both, and abort_job
             *              clears auto_start_jobs server-side as a safety stop. Passing the
             *              live value and the same action the header calls means there is
             *              one source of truth rather than two copies to keep in step. */
            liveToggles={{
              dry_run_mode:    { value: dryRun,    onToggle: toggleDryRun },
              auto_start_jobs: { value: autoStart, onToggle: toggleAutoStart },
            }}
            />
            </div>
          )}

          {page === "review" && (
            <div style={{ flex: 1, overflowY: "auto" }}>
            <ReviewPage api={api} items={review} onRefresh={fetchAll} toast={toast} invalidateHistory={invalidateHistory} reviewRefreshKey={reviewRefreshKey} />
            </div>
          )}

          {page === "forge" && (
            <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
            <ForgePage
            api={api}
            forgeRefreshKey={forgeRefreshKey}
            active={forgeActive}
            processed={forgeProcessed}
            onAdd={forgeAdd}
            onUndo={forgeUndo}
            workerPaused={workerPaused}
            isMobile={isMobile}
            />
            </div>
          )}

          {page === "themes" && (
            <ThemeEditorPage isMobile={isMobile}>
            {/* SettingsPage is the preview subject because it is the
              * densest page for theming: headers, inputs, selects,
              * toggles, buttons, borders and badges all on one screen.
              *
              * onDirtyChange is a no-op rather than setSettingsDirty. The
              * unsaved-changes guard keys off `page === "settings"`, so a
              * preview instance reporting dirty would arm a guard that
              * this route can never disarm, and leaving the editor would
              * prompt about settings the user never opened.
              *
              * It loads real settings from the API. Without a backend
              * running it renders its own error state, which is still
              * drawn from the draft, so the preview stays useful. */}
              <SettingsPage
              api={api}
              toast={toast}
              isMobile={isMobile}
              revertRefreshKey={revertRefreshKey}
              onDirtyChange={() => {}}
              /* Passed here too, unlike onDirtyChange above: the Danger Zone
               *                  in this preview is live and really does clear the database,
               *                  and there is no equivalent reason to make it a no-op. */
              onDatabaseCleared={refreshAllPanels}
              liveToggles={{
                dry_run_mode:    { value: dryRun,    onToggle: toggleDryRun },
                auto_start_jobs: { value: autoStart, onToggle: toggleAutoStart },
              }}
              />
              </ThemeEditorPage>
          )}

          {/* ╔══════════════════════════════════════════════╗
            ║  OVERLAYS                                    ║
            ╚══════════════════════════════════════════════╝ */}
            {modal && (
              <DetailModal
              item={modal}
              isMobile={isMobile}
              onClose={() => setModal(null)}
              onRetry={["failed", "cancelled", "dry_run", "success", "skipped"].includes(modal.status)
                ? () => retryItem(modal) : null}
                retryLabel={["success", "skipped"].includes(modal.status) ? "RE-PROCESS" : "RETRY"}
                onDismiss={["success", "failed", "skipped", "cancelled", "dry_run"].includes(modal.status)
                  ? () => dismissItem(modal) : null}
                  />
            )}
            <ReleaseNotesModal api={api} />
            <Toasts items={toasts} isMobile={isMobile} />
            {pendingNav && (
              <UnsavedChangesModal
              onKeep={() => setPendingNav(null)}
              onDiscard={discardAndLeave}
              />
            )}
            </div>
    );
}
