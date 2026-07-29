import { useState } from "react";
import { C } from "./constants";
import { useAppData } from "./hooks/useAppData";
import { useActions } from "./hooks/useActions";
import { Toasts } from "./components/layout/Toasts";
import { AppHeader } from "./components/header/AppHeader";
import { SettingsPage } from "./components/settings/SettingsPage";
import { ReviewPage } from "./components/review/ReviewPage";
import { ForgePage } from "./components/forge/ForgePage";
import { ActivePanel } from "./components/dashboard/ActivePanel";
import { QueuePanel } from "./components/dashboard/QueuePanel";
import { HistoryPanel } from "./components/dashboard/HistoryPanel";
import { DetailModal } from "./components/DetailModal";

/* ── Unsaved-changes navigation guard modal ─────────────────────────────── */
const UnsavedChangesModal = ({ onKeep, onDiscard }) => (
  <div
    onClick={onKeep}
    style={{
      position: "fixed", inset: 0, zIndex: 100,
      background: "rgba(0,0,0,0.66)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
    }}
  >
    <div
      onClick={e => e.stopPropagation()}
      style={{
        width: "100%", maxWidth: 400,
        background: C.card, border: `1px solid ${C.border}`,
        padding: "22px 22px 18px",
      }}
    >
      <div style={{ color: C.amber, fontSize: 10, letterSpacing: "0.16em", fontWeight: 700, marginBottom: 10 }}>
        UNSAVED CHANGES
      </div>
      <div style={{ color: C.text, fontSize: 13, lineHeight: 1.6, marginBottom: 20 }}>
        You have unsaved settings changes. Leave without saving? Your changes will be lost.
      </div>
      <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
        <button
          onClick={onKeep}
          style={{
            padding: "7px 16px", background: "transparent",
            border: `1px solid ${C.muted}`, color: C.text,
            fontSize: 10, fontFamily: "inherit", fontWeight: 700, letterSpacing: "0.08em", cursor: "pointer",
          }}
        >
          KEEP EDITING
        </button>
        <button
          onClick={onDiscard}
          style={{
            padding: "7px 16px", background: C.red + "22",
            border: `1px solid ${C.red}`, color: C.red,
            fontSize: 10, fontFamily: "inherit", fontWeight: 700, letterSpacing: "0.08em", cursor: "pointer",
          }}
        >
          DISCARD CHANGES
        </button>
      </div>
    </div>
  </div>
);

/* ═══════════════════════════════════════════════════════════════════════════
 *  ROOT APP
 ═ *══════════════════════════════════════════════════════════════════════════ */
export default function App() {
  const data = useAppData();
  const { isMobile } = data;
  const [queueTab, setQueueTab] = useState("queue"); // mobile only
  const {
    api, setApi, page, setPage,
    activeJobs, queue, review,
    modal, setModal,
    toasts,
    dryRun,
    scanning, scanProgress,
    showApiBar, setShowApiBar,
    workerPaused,
    autoStart,
    forgeActive, forgeProcessed, forgeRefreshKey,
      toast, fetchAll,
      pendingQueue, wsConnected, historyRefreshKey, setHistoryRefreshKey,
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
   *  requestPage intercepts nav tab clicks: leaving Settings while dirty
   *  opens a confirm modal instead of navigating. Switching CATEGORIES
   *  inside Settings doesn't route (edits are kept in state), so it isn't
   *  guarded. Browser back/refresh is covered separately by a beforeunload
   *  handler inside SettingsPage. */
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [pendingPage,   setPendingPage]   = useState(null);

  const requestPage = (target) => {
    if (page === "settings" && settingsDirty && target !== "settings") {
      setPendingPage(target);
    } else {
      setPage(target);
    }
  };

  const discardAndLeave = () => {
    const target = pendingPage;
    setSettingsDirty(false);
    setPendingPage(null);
    if (target) setPage(target);
  };

  /* ── Render ───────────────────────────────────────────────────────────── */
  return (
    <div style={{
      height: "100vh",
      display: "flex",
      flexDirection: "column",
      background: C.bg,
      color: C.text,
      fontFamily: "'JetBrains Mono', 'Courier New', monospace",
      fontSize: 13,
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
              borderTop: `1px solid ${C.border}`,
            }}>
            {/* Mobile tab bar */}
            {isMobile && (
              <div style={{
                display: "flex",
                flexShrink: 0,
                borderBottom: `1px solid ${C.border}`,
                background: C.card,
              }}>
              {[["queue", "QUEUE"], ["history", "HISTORY"]].map(([k, l]) => (
                <button
                key={k}
                onClick={() => setQueueTab(k)}
                style={{
                  flex: 1,
                  padding: "10px 0",
                  background: "transparent",
                  border: "none",
                  borderBottom: queueTab === k
                  ? `2px solid ${C.amber}` : "2px solid transparent",
                  color: queueTab === k ? C.amber : C.dim,
                  fontSize: 9,
                  fontFamily: "inherit",
                  letterSpacing: "0.14em",
                  fontWeight: 700,
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
                borderRight: !isMobile ? `1px solid ${C.border}` : "none",
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
          <SettingsPage api={api} toast={toast} isMobile={isMobile} onDirtyChange={setSettingsDirty} />
          </div>
        )}

        {page === "review" && (
          <div style={{ flex: 1, overflowY: "auto" }}>
          <ReviewPage api={api} items={review} onRefresh={fetchAll} toast={toast} setHistoryRefreshKey={setHistoryRefreshKey} />
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
          isMobile={isMobile}
          />
          </div>
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
          <Toasts items={toasts} isMobile={isMobile} />
          {pendingPage && (
            <UnsavedChangesModal
              onKeep={() => setPendingPage(null)}
              onDiscard={discardAndLeave}
            />
          )}
          </div>
  );
}
