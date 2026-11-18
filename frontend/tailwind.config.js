import colors from 'tailwindcss/colors'
import defaultTheme from 'tailwindcss/defaultTheme'

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', ...defaultTheme.fontFamily.sans],
        mono: ['"JetBrains Mono"', ...defaultTheme.fontFamily.mono],
      },
      colors: {
        // Neutros fríos y limpios (base de textos, bordes y fondos).
        gray: colors.slate,
        // Color de marca: azul institucional sobrio. Los componentes usan
        // "indigo-*", así que se redefine aquí para cambiar toda la app.
        indigo: {
          50: '#eff5ff',
          100: '#dbe8fe',
          200: '#bfd6fe',
          300: '#93bbfd',
          400: '#6097f9',
          500: '#3b74f4',
          600: '#2456e0',
          700: '#1c44c4',
          800: '#1d3a9e',
          900: '#1d357d',
          950: '#15214c',
        },
      },
      boxShadow: {
        sm: '0 1px 2px 0 rgb(15 23 42 / 0.05)',
        DEFAULT:
          '0 1px 3px 0 rgb(15 23 42 / 0.07), 0 1px 2px -1px rgb(15 23 42 / 0.05)',
        md: '0 4px 12px -2px rgb(15 23 42 / 0.08), 0 2px 4px -2px rgb(15 23 42 / 0.05)',
        lg: '0 12px 24px -6px rgb(15 23 42 / 0.10), 0 4px 8px -4px rgb(15 23 42 / 0.06)',
        xl: '0 24px 48px -12px rgb(15 23 42 / 0.16)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 0.25s ease-out both',
      },
    },
  },
  plugins: [],
}
