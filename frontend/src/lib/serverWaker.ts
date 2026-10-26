// Wakes a spun-down Render instance with GET /status (up to 42.7 s measured) and lets
// anyone wait for it. Every caller shares one attempt; once awake it stays resolved.
// If every try fails, the next wake() starts over, so "Try again" can recover.

export type ServerWaker = { wake: () => Promise<boolean> }

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export function createServerWaker(
  isUp: () => Promise<boolean>,
  { attempts = 6, delayMs = 5000 }: { attempts?: number; delayMs?: number } = {},
): ServerWaker {
  let pending: Promise<boolean> | null = null

  async function tryUntilUp(): Promise<boolean> {
    for (let attempt = 1; attempt <= attempts; attempt++) {
      if (await isUp()) return true
      if (attempt < attempts) await sleep(delayMs)
    }
    return false
  }

  return {
    wake() {
      pending ??= tryUntilUp().then((up) => {
        if (!up) pending = null
        return up
      })
      return pending
    },
  }
}
