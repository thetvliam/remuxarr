import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ThemeProvider } from "./theme";
// Side-effect import: registers the variable font faces every theme's stacks
// name — one file per family covering the whole weight axis, not one per
// weight. Must come before first paint, hence here rather than in a
// component. See fonts.js for why all themes' fonts load up front, and why
// per-weight files were the wrong shape.
import "./fonts";
// Theme-independent global rules and keyframes. A stylesheet rather than
// an injected <style>, so it applies before React mounts.
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
  <ThemeProvider>
  <App />
  </ThemeProvider>
  </React.StrictMode>
);
