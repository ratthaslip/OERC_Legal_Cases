/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: { sans: ['"IBM Plex Sans Thai"', 'system-ui', 'sans-serif'], mono: ['"IBM Plex Mono"', 'ui-monospace', 'monospace'] },
      boxShadow: { xs: '0 1px 2px rgba(15,23,42,.06)' },
      borderRadius: { xs: '2px' }
    }
  },
  plugins: []
};
