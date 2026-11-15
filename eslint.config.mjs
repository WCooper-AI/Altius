// Lints the interface's ES modules. The main job is no-undef: a name used without an import
// only fails in the browser, on the screen that uses it, so CI catches it here instead.
import js from "@eslint/js";
import globals from "globals";

export default [
  { ignores: ["**/build/**", "**/dist/**", "**/target/**", "**/gen/**", ".venv/**", "data/**"] },
  js.configs.recommended,
  {
    // A leading underscore marks a parameter or binding kept only for its position, e.g. (_, i) => ...
    rules: { "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" }] },
  },
  {
    files: ["engine/src/tradealerts/static/**/*.js"],
    languageOptions: { ecmaVersion: "latest", sourceType: "module", globals: { ...globals.browser } },
  },
  {
    // Plain scripts, not modules: theme.js runs in <head> before the page draws, report.js is the
    // stand-alone report page, and the desktop splash page loads splash.js on its own.
    files: ["engine/src/tradealerts/static/theme.js", "engine/src/tradealerts/static/report.js",
      "selfhost/desktop/splash/**/*.js"],
    languageOptions: { ecmaVersion: "latest", sourceType: "script", globals: { ...globals.browser } },
  },
  {
    files: ["engine/tests/js/**/*.mjs"],
    languageOptions: { ecmaVersion: "latest", sourceType: "module", globals: { ...globals.node } },
  },
];
