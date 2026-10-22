import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { KEEP_ALIVE_MS, useKeepAlive } from './useKeepAlive'
import { CHARS_PER_TICK, TICK_MS, useTypewriter } from './useTypewriter'

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useKeepAlive', () => {
  it('pings every 10 minutes only while active, and stops on cleanup', () => {
    const ping = vi.fn()
    const { rerender, unmount } = renderHook(({ active }) => useKeepAlive(active, ping), { initialProps: { active: false } })
    vi.advanceTimersByTime(30 * 60_000)
    expect(ping).not.toHaveBeenCalled()

    rerender({ active: true })
    vi.advanceTimersByTime(KEEP_ALIVE_MS - 1)
    expect(ping).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(ping).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(KEEP_ALIVE_MS)
    expect(ping).toHaveBeenCalledTimes(2)

    rerender({ active: false })
    vi.advanceTimersByTime(30 * 60_000)
    expect(ping).toHaveBeenCalledTimes(2)

    rerender({ active: true })
    unmount()
    vi.advanceTimersByTime(30 * 60_000)
    expect(ping).toHaveBeenCalledTimes(2)
  })
})

describe('useTypewriter', () => {
  const TEXT = 'How did you validate your survey instrument?'

  it('reveals the text a few characters at a time', () => {
    const { result } = renderHook(() => useTypewriter(TEXT))
    expect(result.current.shown).toBe('')
    act(() => vi.advanceTimersByTime(TICK_MS))
    expect(result.current.shown).toBe(TEXT.slice(0, CHARS_PER_TICK))
    act(() => vi.advanceTimersByTime(TICK_MS * TEXT.length))
    expect(result.current).toMatchObject({ shown: TEXT, done: true })
  })

  it('skips to the full text on a click (skip) or any key', () => {
    const clicked = renderHook(() => useTypewriter(TEXT))
    act(() => clicked.result.current.skip())
    expect(clicked.result.current).toMatchObject({ shown: TEXT, done: true })

    const keyed = renderHook(() => useTypewriter(TEXT))
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }))
    })
    expect(keyed.result.current.shown).toBe(TEXT)
  })

  it('shows the whole text at once under prefers-reduced-motion', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: query === '(prefers-reduced-motion: reduce)' }))
    const { result } = renderHook(() => useTypewriter(TEXT))
    expect(result.current).toMatchObject({ shown: TEXT, done: true })
  })
})
