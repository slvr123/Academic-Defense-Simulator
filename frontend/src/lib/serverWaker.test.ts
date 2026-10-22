import { describe, expect, it, vi } from 'vitest'
import { createServerWaker } from './serverWaker'

describe('createServerWaker', () => {
  it('shares one attempt between callers and stays resolved once up', async () => {
    const isUp = vi.fn(async () => true)
    const waker = createServerWaker(isUp)
    expect(await Promise.all([waker.wake(), waker.wake()])).toEqual([true, true])
    expect(await waker.wake()).toBe(true)
    expect(isUp).toHaveBeenCalledTimes(1)
  })

  it('retries, gives up, and starts over on the next wake', async () => {
    const isUp = vi.fn(async () => false)
    const waker = createServerWaker(isUp, { attempts: 3, delayMs: 0 })
    expect(await waker.wake()).toBe(false)
    expect(isUp).toHaveBeenCalledTimes(3)
    isUp.mockResolvedValue(true)
    expect(await waker.wake()).toBe(true)
    expect(isUp).toHaveBeenCalledTimes(4)
  })
})
