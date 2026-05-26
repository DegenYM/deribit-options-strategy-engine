/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./investor.html",
    "./investor.zh.html",
    "./src/**/*.js",
  ],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
