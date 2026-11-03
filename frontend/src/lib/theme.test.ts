import { describe, expect, it } from 'vitest'
import { applyTheme, currentTheme, readTheme, storeTheme, THEME_STORAGE_KEY } from './theme'

function memoryStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial))
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
    values,
  }
}

const throwing = {
  getItem: (): string | null => {
    throw new DOMException('blocked', 'SecurityError')
  },
  setItem: () => {
    throw new DOMException('full', 'QuotaExceededError')
  },
}

describe('theme persistence (M12 D4)', () => {
  it('is Paper on a first visit, whatever the OS prefers', () => {
    expect(readTheme(memoryStorage())).toBe('paper')
    expect(readTheme(null)).toBe('paper')
  })

  it('reads a stored Chamber choice, and anything else as Paper', () => {
    expect(readTheme(memoryStorage({ [THEME_STORAGE_KEY]: 'chamber' }))).toBe('chamber')
    expect(readTheme(memoryStorage({ [THEME_STORAGE_KEY]: 'paper' }))).toBe('paper')
    expect(readTheme(memoryStorage({ [THEME_STORAGE_KEY]: 'bench' }))).toBe('paper') // the retired theme
  })

  it('falls back to Paper when storage throws, and storing never throws', () => {
    expect(readTheme(throwing)).toBe('paper')
    expect(() => storeTheme('chamber', throwing)).not.toThrow()
    expect(() => storeTheme('chamber', null)).not.toThrow()
  })

  it('stores the choice under its key', () => {
    const storage = memoryStorage()
    storeTheme('chamber', storage)
    expect(storage.values.get(THEME_STORAGE_KEY)).toBe('chamber')
  })

  it('puts the theme on <html> and the browser chrome colour in the meta tag', () => {
    const meta = document.createElement('meta')
    meta.name = 'theme-color'
    document.head.append(meta)
    applyTheme('chamber')
    expect(document.documentElement.dataset.theme).toBe('chamber')
    expect(currentTheme()).toBe('chamber')
    expect(meta.content).toBe('#121214')
    applyTheme('paper')
    expect(currentTheme()).toBe('paper')
    expect(meta.content).toBe('#F5F4F1')
    meta.remove()
  })
})
