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
    extend: {
      colors: {
        slate: {
          850: "#1a2234",
        },
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(-4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.3s ease-out",
      },
    },
  },
  plugins: [],
};
