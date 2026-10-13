/* ═══════════════════════════════════════════════════════════════════════════
 * ACT WARNINGS
 * Catches React's "not wrapped in act(...)" warning so that src/test/setup.js
 * can fail the test that caused it. Tested in src/__tests__/actWarnings.test.js.
 *
 * The warning means a component updated after the test had stopped watching
 * it: usually a fetch started inside a synchronous act() that resolved once
 * act() had returned. Thirty-one tests across five files printed it, 230
 * lines in all, and a real failure's output was buried in them. The test
 * still passed, which is why the warnings had piled up: nothing made anyone
 * look. Failing the test is what keeps the count at zero.
 *
 * One way round it: a test that silences console.error itself (with
 * vi.spyOn(...).mockImplementation) silences this too, because its spy then
 * never calls on to the guard. Several error-path tests do that to keep an
 * expected error out of the log; a warning inside one of them goes unseen.
 ═══════════════════════════════════════════════════════════════════════════ */

const MARK = "not wrapped in act(";

/** Whether a console.error call is React's act warning. */
export const isActWarning = (args) =>
  typeof args[0] === "string" && args[0].includes(MARK);

/** The warning as one line, naming the component that updated. */
export const describeActWarning = (args) =>
  args[0].includes("%s") && typeof args[1] === "string"
    ? `An update to ${args[1]} was not wrapped in act(...)`
    : args[0].split("\n")[0];

/**
 * A replacement for console.error that keeps act warnings back, and passes
 * everything else to `original`. take() returns the warnings seen since the
 * last take(), and forgets them.
 */
export function createActGuard(original) {
  let seen = [];
  return {
    error(...args) {
      if (isActWarning(args)) seen.push(describeActWarning(args));
      else original(...args);
    },
    take() {
      const found = seen;
      seen = [];
      return found;
    },
  };
}
