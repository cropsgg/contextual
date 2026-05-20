import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#141312",
          raised: "#1c1b19",
          border: "#2e2c28",
          muted: "#252320",
        },
        accent: {
          DEFAULT: "#d97757",
          hover: "#e88b6a",
          muted: "#a85a3f",
        },
        ink: {
          DEFAULT: "#eceae4",
          muted: "#9c9890",
          faint: "#6b6760",
        },
        danger: {
          DEFAULT: "#e05c5c",
          muted: "#b84545",
        },
      },
      fontFamily: {
        sans: [
          "var(--font-sans)",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      borderRadius: {
        DEFAULT: "0.375rem",
        lg: "0.5rem",
        xl: "0.625rem",
        "2xl": "0.875rem",
      },
      boxShadow: {
        menu: "0 4px 24px rgba(0, 0, 0, 0.35)",
      },
    },
  },
  plugins: [],
};

export default config;
