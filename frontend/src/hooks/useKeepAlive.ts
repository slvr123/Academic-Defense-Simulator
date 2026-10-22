import { useEffect } from 'react'

// Render free spins an idle instance down after 15 minutes, and sessions live in its
// memory, so someone thinking over an answer could lose the whole defense. While a
// defense is open, a /health call every 10 minutes keeps it up; never otherwise (D10).
export const KEEP_ALIVE_MS = 10 * 60 * 1000

export function useKeepAlive(active: boolean, ping: () => void): void {
  useEffect(() => {
    if (!active) return
    const timer = setInterval(ping, KEEP_ALIVE_MS)
    return () => clearInterval(timer)
  }, [active, ping])
}
