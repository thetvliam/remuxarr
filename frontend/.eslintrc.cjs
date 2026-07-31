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
  plugins: ["react-hooks"],
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
  },
  ignorePatterns: ["dist", "node_modules"],
};
