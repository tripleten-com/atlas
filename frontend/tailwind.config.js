/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Inter Variable"', "Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      colors: {
        // Atlas brand: a deep clinical teal
        brand: {
          50: "#eefbf8",
          100: "#d4f4ec",
          200: "#aee8db",
          300: "#7ad5c4",
          400: "#45b9a8",
          500: "#25998b",
          600: "#197b71",
          700: "#16635c",
          800: "#15504b",
          900: "#14423f",
          950: "#062624",
        },
        ink: {
          50: "#f6f7f9",
          100: "#eceef2",
          200: "#dcdfe6",
          300: "#c1c6d1",
          400: "#9199a8",
          500: "#6b7385",
          600: "#515969",
          700: "#3e4553",
          800: "#272c36",
          900: "#171b22",
          950: "#0c1016",
        },
        critical: { 50: "#fdf2f2", 100: "#fbe1e1", 600: "#c42b2b", 700: "#a32121" },
        caution: { 50: "#fdf8ec", 100: "#faedcc", 600: "#b0740a", 700: "#8a5a00" },
        success: { 50: "#eefaf3", 100: "#d5f2e1", 600: "#1f8a50", 700: "#186e40" },
        info: { 50: "#eff5ff", 100: "#dce9fe", 600: "#2f63d8", 700: "#244fb0" },
      },
      boxShadow: {
        card: "0 1px 2px rgba(16, 24, 40, 0.04), 0 1px 3px rgba(16, 24, 40, 0.06)",
        pop: "0 12px 32px -8px rgba(16, 24, 40, 0.18), 0 4px 8px -4px rgba(16, 24, 40, 0.08)",
      },
      keyframes: {
        pulseDot: { "0%, 100%": { opacity: 1 }, "50%": { opacity: 0.35 } },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        fadeIn: { from: { opacity: 0, transform: "translateY(2px)" }, to: { opacity: 1, transform: "none" } },
      },
      animation: {
        "pulse-dot": "pulseDot 1.6s ease-in-out infinite",
        shimmer: "shimmer 1.4s infinite",
        "fade-in": "fadeIn 180ms ease-out",
      },
    },
  },
  plugins: [],
};
