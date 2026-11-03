// The header's theme switch (M12 D4): one button, pressed when Chamber (dark) is on.
import { useState } from 'react'
import { applyTheme, currentTheme, storeTheme, type Theme } from '../lib/theme'

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme)
  const dark = theme === 'chamber'

  const toggle = () => {
    const next: Theme = dark ? 'paper' : 'chamber'
    applyTheme(next)
    storeTheme(next)
    setTheme(next)
  }

  return (
    <button
      type="button"
      aria-pressed={dark}
      aria-label="Dark theme"
      title={dark ? 'Switch to the light theme' : 'Switch to the dark theme'}
      onClick={toggle}
      className="flex size-11 shrink-0 items-center justify-center rounded-[10px] border border-line-strong bg-card text-ink transition-colors hover:border-action focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-action motion-reduce:transition-none print:hidden"
    >
      {dark ? <SunIcon /> : <MoonIcon />}
    </button>
  )
}

function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
    </svg>
  )
}

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  )
}
