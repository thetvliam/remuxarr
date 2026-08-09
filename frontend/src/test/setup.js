// Vitest setup — runs before every test file.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  // Unmount anything a test rendered. Without this a component left mounted
  // keeps its effects (and its timers and fetch calls) alive into the next
  // test, which is the classic source of order-dependent frontend suites —
  // the backend suite is explicitly free of inter-file ordering dependence
  // and this one should start out the same way.
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
