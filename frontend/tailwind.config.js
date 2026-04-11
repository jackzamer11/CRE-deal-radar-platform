/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Dark navy base
        surface: {
          DEFAULT: '#0B0F1A',
          card:    '#131929',
          border:  '#1E2A3A',
          muted:   '#1A2236',
          hover:   '#1E2D42',
        },
        // Text
        ink: {
          primary:   '#F1F5F9',
          secondary: '#94A3B8',
          muted:     '#64748B',
        },
        // Accent
        accent: {
          blue:   '#3B82F6',
          blueDim: '#1D4ED8',
        },
        // Signal colors
        signal: {
          immediate: '#EF4444',   // Red — act now
          high:      '#F59E0B',   // Amber — this week
          workable:  '#3B82F6',   // Blue — monitor
          ignore:    '#374151',   // Gray
        },
        // Deal type
        deal: {
          preMkt:   '#8B5CF6',    // Purple
          mispriced:'#F59E0B',   // Amber
          tenant:   '#10B981',   // Green
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
