const typography = require('@tailwindcss/typography')
const animate = require('tailwindcss-animate')

function cssColor(name) {
  return ({ opacityValue }) => {
    const numericOpacity = Number(opacityValue)
    if (!Number.isFinite(numericOpacity)) return `var(--${name})`
    const opacityKey = String(Math.round(numericOpacity * 1000) / 10).replace('.', '_')
    return `rgb(var(--${name}-rgb) / var(--${name}-opacity-${opacityKey}, ${opacityValue}))`
  }
}

module.exports = {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        background: cssColor('background'),
        foreground: cssColor('foreground'),
        card: cssColor('card'),
        'card-foreground': cssColor('card-foreground'),
        popover: cssColor('popover'),
        'popover-foreground': cssColor('popover-foreground'),
        primary: cssColor('primary'),
        'primary-foreground': cssColor('primary-foreground'),
        secondary: cssColor('secondary'),
        'secondary-foreground': cssColor('secondary-foreground'),
        muted: cssColor('muted'),
        'muted-foreground': cssColor('muted-foreground'),
        placeholder: cssColor('placeholder'),
        accent: cssColor('accent'),
        'accent-foreground': cssColor('accent-foreground'),
        destructive: cssColor('destructive'),
        'destructive-foreground': cssColor('destructive-foreground'),
        border: cssColor('border'),
        input: cssColor('input'),
        ring: cssColor('ring'),
        overlay: cssColor('overlay'),
        'overlay-strong': cssColor('overlay-strong'),
        success: cssColor('success'),
        'success-foreground': cssColor('success-foreground'),
        warning: cssColor('warning'),
        'warning-foreground': cssColor('warning-foreground'),
        info: cssColor('info'),
        'info-foreground': cssColor('info-foreground'),
        'success-soft': cssColor('success-soft'),
        'success-soft-fg': cssColor('success-soft-fg'),
        'warning-soft': cssColor('warning-soft'),
        'warning-soft-fg': cssColor('warning-soft-fg'),
        'destructive-soft': cssColor('destructive-soft'),
        'destructive-soft-fg': cssColor('destructive-soft-fg'),
        'info-soft': cssColor('info-soft'),
        'info-soft-fg': cssColor('info-soft-fg'),
        'neutral-soft': cssColor('neutral-soft'),
        'neutral-soft-fg': cssColor('neutral-soft-fg'),
        'cat-blue': cssColor('cat-blue'),
        'cat-blue-fg': cssColor('cat-blue-fg'),
        'cat-teal': cssColor('cat-teal'),
        'cat-teal-fg': cssColor('cat-teal-fg'),
        'cat-violet': cssColor('cat-violet'),
        'cat-violet-fg': cssColor('cat-violet-fg'),
        'cat-amber': cssColor('cat-amber'),
        'cat-amber-fg': cssColor('cat-amber-fg'),
        sidebar: cssColor('sidebar'),
        'sidebar-foreground': cssColor('sidebar-foreground'),
        'sidebar-primary': cssColor('sidebar-primary'),
        'sidebar-primary-foreground': cssColor('sidebar-primary-foreground'),
        'sidebar-accent': cssColor('sidebar-accent'),
        'sidebar-accent-foreground': cssColor('sidebar-accent-foreground'),
        'sidebar-border': cssColor('sidebar-border'),
        'sidebar-ring': cssColor('sidebar-ring'),
        'chart-1': cssColor('chart-1'),
        'chart-2': cssColor('chart-2'),
        'chart-3': cssColor('chart-3'),
        'chart-4': cssColor('chart-4'),
        'chart-5': cssColor('chart-5'),
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'SF Mono', 'JetBrains Mono', 'Menlo', 'Consolas', 'monospace'],
        heading: ['-apple-system', 'BlinkMacSystemFont', 'PingFang SC', 'Microsoft YaHei', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
      borderRadius: {
        sm: 'var(--radius-compact)',
        md: 'var(--radius-control)',
        lg: 'var(--radius-card)',
        xl: 'var(--radius-overlay)',
      },
      boxShadow: {
        card: 'var(--shadow-card)',
        pop: 'var(--shadow-pop)',
        btn: 'var(--shadow-btn)',
      },
      ringWidth: {
        3: '3px',
      },
      opacity: {
        8: '.08',
      },
      blur: {
        xs: '4px',
      },
      minWidth: {
        28: '7rem',
        36: '9rem',
      },
    },
  },
  plugins: [typography, animate],
}
