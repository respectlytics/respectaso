/**
 * Tailwind CSS v3.4.x config - compiled via scripts/build_css.sh (standalone CLI, no Node).
 *
 * Content globs must cover EVERY file that contains Tailwind class literals,
 * including Python (aso/scoring.py, aso/models.py, aso/templatetags/) and JS.
 * When adding a new app or template directory, extend the globs below and rebuild.
 *
 * RULE: always write complete class names in code - never concatenate fragments
 * like 'text-' + color + '-400'. The content scanner only extracts whole literals.
 *
 * In the public (Free) repo the aso_pro/_public_overrides globs match nothing -
 * that is harmless; the committed static/css/tailwind.css (built in Pro, a
 * superset) is what both editions actually ship.
 */
module.exports = {
  content: [
    "./aso/templates/**/*.html",
    "./aso_pro/templates/**/*.html",
    "./_public_overrides/**/*.html",
    "./static/js/**/*.js",
    "./aso/**/*.py",
    "./aso_pro/**/*.py",
  ],
  theme: {
    // The one typeface, bundled in static/fonts/ (tailwind.source.css):
    // Inter for everything, titles included, and the system monospace for
    // keyword fields and commands. Set here, not under extend, so Tailwind's
    // own serif family does not exist and font-serif cannot be asked for.
    // System fonts only as fallbacks. docs/development/SELF_HOSTED_FONTS_PLAN.md
    fontFamily: {
      sans: ['"Inter"', "ui-sans-serif", "system-ui", "-apple-system", '"Segoe UI"', "Roboto", '"Helvetica Neue"', "Arial", "sans-serif", '"Apple Color Emoji"', '"Segoe UI Emoji"'],
      mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", '"Liberation Mono"', '"Courier New"', "monospace"],
    },
    extend: {
      colors: {
        slate: {
          850: "#1a2234",
        },
      },
      // The type scale, set once (docs/development/UI_FOUNDATIONS_PLAN.md,
      // UI_REDESIGN_PLAN.md 12.1). The root is 14px (base.html), so the rem
      // values are written for it: 12px is 0.8571rem. Nothing smaller than
      // text-2xs (11px) exists, and no template, script or Python string uses
      // an arbitrary text-[Npx].
      fontSize: {
        "2xs": ["0.7857rem", { lineHeight: "1.1429rem" }], // 11px / 16px
        xs: ["0.8571rem", { lineHeight: "1.1429rem" }],    // 12px / 16px
        sm: ["0.9286rem", { lineHeight: "1.3571rem" }],    // 13px / 19px
        base: ["1rem", { lineHeight: "1.5rem" }],          // 14px / 21px
        lg: ["1.1429rem", { lineHeight: "1.7143rem" }],    // 16px / 24px
        xl: ["1.4286rem", { lineHeight: "1.9286rem" }],    // 20px / 27px
        "2xl": ["1.7143rem", { lineHeight: "2.1429rem" }], // 24px / 30px
      },
      keyframes: {
        // A Tracked Keywords row that a search just added or the daily update
        // just changed blinks twice, so the reader sees it land (2026-10-02).
        "row-blink": {
          "0%, 40%, 80%, 100%": { backgroundColor: "rgba(168, 85, 247, 0)" },
          "20%, 60%": { backgroundColor: "rgba(168, 85, 247, 0.28)" },
        },
        // The Activity pill's one pulse when a started task's note lands in it.
        "pill-pulse": {
          "0%": { boxShadow: "0 0 0 0 rgba(168, 85, 247, 0.5)" },
          "100%": { boxShadow: "0 0 0 10px rgba(168, 85, 247, 0)" },
        },
        "fade-in": {
          from: { opacity: "0", transform: "translateY(-4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "pill-pulse": "pill-pulse 0.6s ease-out 1",
        "row-blink": "row-blink 1.6s ease-in-out 1",
        "fade-in": "fade-in 0.3s ease-out",
      },
    },
  },
  plugins: [],
};
