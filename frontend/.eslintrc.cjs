/* Deliberately narrow. This is not a style linter — formatting is not
 * enforced here and no opinionated preset is extended, so it will not
 * generate churn or argue about quotes.
 *
 * It exists for the two React Hooks rules. The codebase already contains
 * `eslint-disable-line react-hooks/exhaustive-deps` comments written as if
 * the rule were running; it was not, and a dependency array that had been
 * correct for a module constant went stale when that constant became a
 * context value. Nothing caught it. This does.
 */
module.exports = {
  root: true,
  env: { browser: true, es2022: true },
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  plugins: ["react-hooks", "react"],
  extends: [
    // React correctness only. jsx-runtime switches off the rules that exist
    // solely for the pre-17 transform, so React does not have to be imported
    // into every file just to satisfy the linter.
    "plugin:react/recommended",
    "plugin:react/jsx-runtime",
  ],
  settings: { react: { version: "detect" } },
  rules: {
    // A hook called conditionally or after an early return breaks React's
    // internal ordering. Always an error — there is no valid reason to ship
    // one, and the failure mode is intermittent rather than immediate.
    "react-hooks/rules-of-hooks": "error",
    // A missing dependency is a value frozen at the time the callback was
    // last built. It compiles, renders, and passes any snapshot test, then
    // silently serves stale data. Error rather than warn: warnings in a
    // project with no CI lint step are indistinguishable from silence.
    "react-hooks/exhaustive-deps": "error",
    // An unused import or variable is usually the residue of a refactor that
    // did not finish. ApiBar carried alpha and ALPHA for weeks after the last
    // use of either, and it took a human reading the file to notice.
    // Arguments are exempt: a handler ignoring its event, or a component
    // destructuring a prop it does not yet use, is not a mistake.
    "no-unused-vars": ["error", { args: "none", varsIgnorePattern: "^_" }],
    // Off deliberately. It demands runtime PropTypes declarations, which this
    // codebase does not use and React itself has moved away from — enabling it
    // means 300+ annotations that duplicate the destructuring one line below
    // and go stale independently of it. The rest of react/recommended stays
    // on: those rules catch real mistakes rather than missing paperwork.
    "react/prop-types": "off",
    // Narrowed to the two characters that are actually ambiguous in JSX.
    // The default also forbids ' and ", which in practice only flags prose —
    // can't, don't, the panel's Failed tab — and escaping those to &apos;
    // makes the source materially harder to read for no rendering benefit.
    // > and } stay forbidden because they can silently change what parses.
    "react/no-unescaped-entities": ["error", { forbid: [">", "}"] }],
  },
  ignorePatterns: ["dist", "node_modules"],
};
