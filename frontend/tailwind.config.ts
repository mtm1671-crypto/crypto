import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        neuro: {
          50: "#fef6ee",
          100: "#fce8d0",
          200: "#f8cfa0",
          300: "#f0b070",
          400: "#e8975e",
          500: "#d4753a",
          600: "#b06830",
          700: "#8e4f22",
          800: "#6e3c1a",
          900: "#4e2a14",
          950: "#2e180c",
        },
      },
    },
  },
  plugins: [],
};

export default config;
