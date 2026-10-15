import { useState, useEffect, useCallback, useRef } from "react";

/* ── Routing helpers ──────────────────────────────────────────────────────── */

/* "themes" is the developer theme editor. It is deliberately absent from
 * AppHeader's NAV_ITEMS, so nothing in the UI links to it, but it IS listed
 * here — a page id missing from this set resolves to "dashboard", so leaving
 * it out would make the route unreachable even in a dev checkout.
 *
 * That makes it undiscoverable rather than unreachable: typing the fragment
 * still gets there in a shipped container. Chosen knowingly over a
 * build-time exclusion. The editor has no backend — saving is a download the
 * developer pastes into theme.jsx by hand — so there is no endpoint behind
 * this and nothing it can write to. Tightening it later means guarding this
 * entry and the App.jsx branch behind import.meta.env.DEV, which would be
 * the codebase's first use of that mechanism. */
const VALID_PAGES = new Set(["dashboard", "settings", "review", "forge", "themes"]);

// Read the current page from the URL hash.
// Falls back to "dashboard" for any unknown or missing hash so the app
// always lands somewhere sensible on a direct visit or a stale bookmark.
const _pageFromHash = () => {
  const hash = window.location.hash.slice(1); // strip leading #
  return VALID_PAGES.has(hash) ? hash : "dashboard";
};

/* ═══════════════════════════════════════════════════════════════════════════
 *  useRouting
 *  Which page is showing, whether the detail modal is open, and what Back
 *  does about either. Client-side routing is implemented here via the
 *  browser History API. Two pieces of state contribute history entries:
 *
 *  • Page navigation  →  #dashboard, #settings, #review, #forge, #themes
 *  • Modal open/close →  same URL, different state object ({ modal: true })
 *
 *  Wrapping setPage and setModal here means every caller (AppHeader,
 *  useActions, App.jsx) gets correct back-button behaviour automatically —
 *  no caller needs to change.
 *
 *  Back can also be REFUSED, via the guard App.jsx registers with
 *  registerNavGuard. Declining a nav tab click just means not calling
 *  setPage; declining a Back means undoing one the browser has already
 *  performed, which is why that lives down here with the popstate listener
 *  rather than in the component that knows why it wants to refuse.
 *
 *  This used to be written inline in useAppData, and is still reached
 *  through it: useAppData calls this hook and returns its values unchanged,
 *  so App, useActions and the tests read them from there as before. It was
 *  split out because nothing here reads server state, and the server state
 *  reads only `page`, to fetch Forge's on entering that page.
 ═══════════════════════════════════════════════════════════════════════════ */
export function useRouting() {
  // ── Routing refs ──────────────────────────────────────────────────────────
  // pageRef mirrors the `page` state value synchronously so setModal can
  // read the current page without a stale closure.
  const pageRef         = useRef(_pageFromHash());
  // modalRef mirrors the `modal` state value synchronously so setModal can
  // detect whether a modal is already open (avoids pushing duplicate history
  // entries when an open modal's contents are replaced), and enrichModal can
  // tell which item is on screen.
  const modalRef        = useRef(null);
  // closedByUserRef is a one-shot flag: set true when setModal(null) triggers
  // history.back() programmatically so the resulting popstate event knows the
  // modal was already closed and doesn't try to close it a second time.
  const closedByUserRef = useRef(false);
  // navGuardRef holds an optional (targetPage) => boolean set by App.jsx. It is
  // consulted on Back, and returning true cancels that navigation. A ref rather
  // than state because the popstate handler is registered once with [] deps and
  // would otherwise read whichever guard existed at mount — permanently the
  // no-op one, since the guard depends on state that only exists later.
  const navGuardRef     = useRef(null);
  // Companion one-shot to closedByUserRef, for the other direction: set when
  // leaveGuarded() re-issues the Back the guard just cancelled, so the handler
  // lets it through instead of asking again and blocking forever.
  const skipGuardRef    = useRef(false);

  // Initialize page from the URL hash so direct visits and refreshes land on
  // the correct tab (e.g. http://remuxarr:8080/#settings → Settings tab).
  const [page,       setPageState]  = useState(_pageFromHash);
  const [modal,      setModalState] = useState(null);

  /* ── Routing: initial replaceState ───────────────────────────────────────
   *    Rewrite the browser's first history entry to the page being shown, with
   *    the app's own state object. A stale or mistyped fragment, or none at
   *    all, is corrected in the address bar (#nonsense becomes #dashboard), so
   *    the URL names what is on screen.
   *
   *    It was also once needed for Back: a null event.state sent the handler
   *    to "dashboard" whatever the URL said. The handler now reads a stateless
   *    entry's page from its URL (see the popstate handler below), so that
   *    reason no longer holds. */
  useEffect(() => {
    const initial = _pageFromHash();
    pageRef.current = initial;
    setPageState(initial);
    window.history.replaceState(
      { page: initial, modal: false },
      "",
      `#${initial}`,
    );
  }, []);

  /* ── Routing: popstate (browser/Android back button) ─────────────────────
   *    Handles two cases:
   *    1. Back from modal state  → close the modal, stay on the same page
   *    2. Back from page navigation → navigate to the previous page            */
  useEffect(() => {
    const handler = (event) => {
      // The closedByUserRef flag is set when setModal(null) calls
      // history.back() programmatically — we've already handled the close
      // in React state, so skip the event to avoid a double update.
      if (closedByUserRef.current) {
        closedByUserRef.current = false;
        return;
      }

      const hasState = event.state != null;
      const state = event.state ?? {};

      /* event.state is the state of the entry being navigated TO, not the
       * one being left. Going back from an open modal therefore lands on
       * the { modal: false } page entry and is handled by the else branch —
       * this if only fires going FORWARD into a modal entry, where it
       * closes the modal rather than reopening it.
       *
       * Behaviour is fine either way, since forward-into-a-modal is not a
       * flow the app produces. The comments said the opposite, which
       * matters because closedByUserRef below is reasoned about in terms of
       * this model. */
      if (state.modal) {
        // Forward navigation INTO a modal entry.
        modalRef.current = null;
        setModalState(null);
      } else {
        // Back to a page entry, including back out of an open modal.
        //
        // Two different fallbacks, deliberately not merged. A history entry
        // the app never created carries NO state object — a manually edited
        // fragment, or an in-page anchor — and for those the URL is the only
        // record of where the user is, so honour it. _pageFromHash() already
        // does exactly this on initial load; the handler simply was not
        // reusing it, so it forced "dashboard" and navigated the app away
        // from the page the URL still named, leaving state and URL
        // disagreeing with every later Back press compounding it.
        //
        // A state object that IS present but names an invalid page is a
        // different situation: the app created that entry, so its page value
        // is authoritative — just stale, most plausibly an entry from a
        // version where that page existed. "dashboard" is the right answer
        // there, and reaching for the URL would be trusting a fragment the
        // same stale entry wrote.
        const target = VALID_PAGES.has(state.page)
        ? state.page
        : (hasState ? "dashboard" : _pageFromHash());

        /* Ask before applying it. Nav tab clicks go through App's requestPage
         * and can simply decline to call setPage, but Back has already
         * happened by the time this fires — the entry is popped and the URL is
         * updated — so declining means putting it back.
         *
         * pushState rather than forward(): forward() is asynchronous and only
         * works if the popped entry is still ahead in the stack, which is not
         * true once the user has navigated anywhere else. Pushing the page we
         * are staying on restores both the URL and a matching state object
         * synchronously, and leaves Back working normally afterwards.
         *
         * beforeunload does NOT cover this. It fires for a refresh or a tab
         * close, which is a document unload; hash and pushState routing never
         * unload the document, so pressing Back on a dirty Settings page
         * discarded the edits with no prompt at all. */
        if (skipGuardRef.current) {
          skipGuardRef.current = false;
        } else if (navGuardRef.current?.(target)) {
          window.history.pushState(
            { page: pageRef.current, modal: false },
            "",
            `#${pageRef.current}`,
          );
          return;
        }

        pageRef.current = target;
        setPageState(target);
        // Also close any open modal — defensive, shouldn't normally be open
        modalRef.current = null;
        setModalState(null);
      }
    };

    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);

  /* ── Routing: wrapped setPage ────────────────────────────────────────────
   *    Called by AppHeader nav tabs. Pushes a new history entry so the back
   *    button can return to the previous tab. */
  const setPage = useCallback((newPage) => {
    // Clicking the tab you are already on pushed another identical entry,
    // so Back then needed one press per click before it did anything
    // visible — the button looked broken rather than slow.
    if (pageRef.current === newPage) return;
    pageRef.current = newPage;
    setPageState(newPage);
    window.history.pushState(
      { page: newPage, modal: false },
      "",
      `#${newPage}`,
    );
  }, []);

  /* ── Routing: navigation guard ───────────────────────────────────────────
   *    App.jsx registers a predicate here so Back can be refused the same way
   *    a nav tab click can. Kept in this hook because the popstate listener
   *    lives here and there is otherwise no path from it to App's state.
   *
   *    The guard is a predicate, not a handler: it answers "should this be
   *    blocked", and opening whatever prompt explains why is the caller's
   *    business. That keeps the routing here free of any knowledge of
   *    Settings, dirty edits, or modals. */
  const registerNavGuard = useCallback((fn) => {
    navGuardRef.current = fn;
    return () => { if (navGuardRef.current === fn) navGuardRef.current = null; };
  }, []);

  /* Re-issue the Back that the guard just cancelled, once the user has said to
   * go anyway. Deliberately history.back() rather than setPage(target): the
   * entry the user was heading for is still there behind the one pushed to
   * cancel, so going back reaches it AND removes that pushed entry. Calling
   * setPage would push a third entry instead, leaving two dead Settings
   * entries to press Back through afterwards. */
  const leaveGuarded = useCallback(() => {
    skipGuardRef.current = true;
    window.history.back();
  }, []);

  /* ── Routing: wrapped setModal ───────────────────────────────────────────
   *    Handles three cases:
   *    • Opening a new modal  → push a history entry (modal: true)
   *    • Replacing an open modal's contents → no push
   *    • Closing the modal    → history.back() removes the modal history entry
   *    A record that arrives over the network goes through enrichModal below
   *    instead, which first checks the modal is still showing that item.  */
  const setModal = useCallback((item) => {
    if (item === null) {
      // Only act if a modal is currently open.
      if (modalRef.current !== null) {
        closedByUserRef.current = true; // suppress the upcoming popstate
        modalRef.current = null;
        setModalState(null);
        window.history.back(); // remove the modal history entry
      }
      return;
    }

    const wasOpen = modalRef.current !== null;
    modalRef.current = item;
    setModalState(item);

    // First open only: push a history entry so the back button can close it.
    // Replacing the contents of a modal already open skips the push: one
    // modal, one entry, one Back to close it.
    if (!wasOpen) {
      window.history.pushState(
        { page: pageRef.current, modal: true },
        "",
        `#${pageRef.current}`,
      );
    }
  }, []);

  /* ── Routing: enrichModal ────────────────────────────────────────────────
   *    Replace the open modal's contents with a fuller record of the same
   *    item, adding no history entry: openDetail's second step, once the
   *    full record arrives. Ignored unless the modal still shows that item,
   *    because by the time the response lands the user may have closed it or
   *    opened another row. Applying it regardless reopened a closed modal,
   *    with a second history entry, and could put the earlier row's file in
   *    front of the one just opened. */
  const enrichModal = useCallback((item) => {
    if (modalRef.current?.id !== item?.id) return;
    modalRef.current = item;
    setModalState(item);
  }, []);

  return { page, setPage, registerNavGuard, leaveGuarded, modal, setModal, enrichModal };
}
