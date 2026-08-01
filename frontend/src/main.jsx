import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ThemeProvider } from "./theme";
// Side-effect import: registers @font-face for every weight any theme
// names. Must come before first paint, hence here rather than in a
// component. See fonts.js for why all themes' fonts load up front.
import "./fonts";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>
);
