// Wakes a spun-down Render instance with GET /status (up to 42.7 s measured) and lets
// anyone wait for it. Every caller shares one attempt; once awake it stays resolved for
// `staleAfterMs`. Render free spins an instance down after 15 idle minutes, so a wake
// after longer than that since the server last answered probes again (M12 D5) rather
// than trusting an old answer. If every try fails, the next wake() starts over, so
// "Try again" can recover.

export type ServerWaker = { wake: () => Promise<boolean> }

// Just under Render's 15-minute spin-down.
export const STALE_AFTER_MS = 14 * 60 * 1000

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export function createServerWaker(
  isUp: () => Promise<boolean>,
  {
    attempts = 6,
    delayMs = 5000,
    staleAfterMs = STALE_AFTER_MS,
    now = Date.now,
  }: { attempts?: number; delayMs?: number; staleAfterMs?: number; now?: () => number } = {},
): ServerWaker {
  let pending: Promise<boolean> | null = null
  let upAt: number | null = null

  async function tryUntilUp(): Promise<boolean> {
    for (let attempt = 1; attempt <= attempts; attempt++) {
      if (await isUp()) return true
      if (attempt < attempts) await sleep(delayMs)
    }
    return false
  }

  return {
    wake() {
      if (upAt !== null && now() - upAt > staleAfterMs) {
        pending = null
        upAt = null
      }
      pending ??= tryUntilUp().then((up) => {
        if (up) upAt = now()
        else pending = null
        return up
      })
      return pending
    },
  }
}
