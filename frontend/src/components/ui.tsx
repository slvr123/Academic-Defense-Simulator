// Small building blocks shared by every screen.
import type { ButtonHTMLAttributes, ReactNode } from 'react'

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-xl border border-line bg-card p-4 sm:p-6 ${className}`}>{children}</section>
}

export function Heading({ children }: { children: ReactNode }) {
  return <h2 className="font-serif text-xl leading-snug">{children}</h2>
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' }

export function Button({ variant = 'primary', className = '', ...props }: ButtonProps) {
  const look =
    variant === 'primary'
      ? 'bg-brand text-white hover:bg-brand/90 disabled:bg-muted/40'
      : 'border border-line bg-card text-ink hover:bg-paper disabled:text-muted'
  return (
    <button
      type="button"
      className={`rounded-lg px-4 py-2.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${look} ${className}`}
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
        <div className="h-full rounded-full bg-brand transition-[width] duration-500" style={{ width: `${percent}%` }} />
      </div>
      <p className="mt-2 text-sm text-muted">{percent}%</p>
    </div>
  )
}

/** A slow pulse that says "working" next to the words that say what. Never shown alone. */
export function Working() {
  return <span aria-hidden className="inline-block size-2.5 animate-pulse rounded-full bg-brand motion-reduce:animate-none" />
}
