// Vitest setup — runs before every test file.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { createActGuard } from "./actWarnings";

/* Fails any test that makes React print its "not wrapped in act(...)"
 * warning, naming the component. Exported so actWarnings.test.js can show it
 * is installed. See src/test/actWarnings.js for why, and for the one way a
 * warning can still get past it. */
export const actGuard = createActGuard(console.error.bind(console));
console.error = actGuard.error;

afterEach(() => {
  // Unmount anything a test rendered. Without this a component left mounted
  // keeps its effects (and its timers and fetch calls) alive into the next
  // test, which is the classic source of order-dependent frontend suites —
  // the backend suite is explicitly free of inter-file ordering dependence
  // and this one should start out the same way.
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();

  const warnings = actGuard.take();
  if (warnings.length) {
    throw new Error(
      "React updated outside act(): something the test started (a fetch, a timer)\n"
      + "finished after the test stopped watching. Await it, or wrap it in an async act().\n  "
      + [...new Set(warnings)].join("\n  "));
  }
});
