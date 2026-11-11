/** @type {import('tailwindcss').Config} */
// Tailwind يقرأ أسماء الكلاسات من main.py (فيه كل القوالب) فيبني CSS لما يُستعمل فقط.
module.exports = {
  content: ["./main.py"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Tajawal", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
