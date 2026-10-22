import { useCallback, useEffect, useState } from 'react'

// The question arrives whole; this reveals it a few characters at a time (D9). It is
// not streaming and must never be called that. A click or a key shows it all at once,
// and with reduced motion it appears at once.
export const CHARS_PER_TICK = 2
export const TICK_MS = 20

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Mount once per text (give the component a key), so each question starts from zero. */
export function useTypewriter(text: string): { shown: string; done: boolean; skip: () => void } {
  const [count, setCount] = useState(() => (prefersReducedMotion() ? text.length : 0))
  const done = count >= text.length
  const skip = useCallback(() => setCount(text.length), [text.length])

  useEffect(() => {
    if (done) return
    const timer = setInterval(() => setCount((c) => Math.min(text.length, c + CHARS_PER_TICK)), TICK_MS)
    return () => clearInterval(timer)
  }, [done, text.length])

  useEffect(() => {
    if (done) return
    window.addEventListener('keydown', skip)
    return () => window.removeEventListener('keydown', skip)
  }, [done, skip])

  return { shown: text.slice(0, count), done, skip }
}
