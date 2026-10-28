// Small building blocks shared by every screen.
import type { ButtonHTMLAttributes, ReactNode } from 'react'

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-2xl border border-line-strong bg-card p-5 sm:p-8 ${className}`}>{children}</section>
}

export function Heading({ children }: { children: ReactNode }) {
  return <h2 className="font-serif text-2xl leading-snug font-medium">{children}</h2>
}

/** Small uppercase mono label, as the Bench design marks sections ("Dr. Reyes asks"). */
export function Eyebrow({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <span className={`font-mono text-xs tracking-[0.12em] uppercase ${className}`}>{children}</span>
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' }

export function Button({ variant = 'primary', className = '', ...props }: ButtonProps) {
  const look =
    variant === 'primary'
      ? 'bg-accent font-semibold text-on-accent hover:bg-accent-hover disabled:bg-rail disabled:text-muted'
      : 'border border-line-strong bg-card text-ink hover:border-accent disabled:text-subtle'
  return (
    <button
      type="button"
      className={`min-h-11 rounded-xl px-5 text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed motion-reduce:transition-none ${look} ${className}`}
      {...props}
    />
  )
}

/** A determinate bar; `value` is 0-1. */
export function ProgressBar({ value, label }: { value: number; label: string }) {
  const percent = Math.round(Math.min(1, Math.max(0, value)) * 100)
  return (
    <div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="h-2 w-full overflow-hidden rounded-full bg-line"
      >
        <div className="h-full rounded-full bg-accent transition-[width] duration-500 motion-reduce:transition-none" style={{ width: `${percent}%` }} />
      </div>
      <p className="mt-2 font-mono text-xs text-muted">{percent}%</p>
    </div>
  )
}

/** A slow pulse that says "working" next to the words that say what. Never shown alone. */
export function Working() {
  return <span aria-hidden className="inline-block size-2.5 animate-pulse rounded-full bg-accent motion-reduce:animate-none" />
}

/** The document icon the Bench design puts beside anything about the candidate's paper. */
export function DocumentIcon({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <path d="M14 3v6h6" />
    </svg>
  )
}
