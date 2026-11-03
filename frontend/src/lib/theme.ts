// M12 D4: Paper (light, the default on every first visit, whatever the OS prefers) and
// Chamber (dark). The theme is the data-theme attribute on <html>, which index.css reads;
// index.html's inline script sets it before first paint so a Chamber visitor never sees
// a flash of Paper. The viewer's choice is kept in localStorage, which can be missing or
// throw (private windows, blocked site data): every read and write is guarded, and any
// failure means Paper.

export type Theme = 'paper' | 'chamber'

export const THEME_STORAGE_KEY = 'ads-theme'
export const DEFAULT_THEME: Theme = 'paper'

/** The browser chrome colour per theme: each theme's page background. */
export const THEME_COLOR: Record<Theme, string> = { paper: '#F5F4F1', chamber: '#121214' }

type ThemeStorage = Pick<Storage, 'getItem' | 'setItem'>

function browserStorage(): ThemeStorage | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

/** The stored choice, or Paper when there is none, it's unreadable, or it isn't a theme. */
export function readTheme(storage: ThemeStorage | null = browserStorage()): Theme {
  try {
    return storage?.getItem(THEME_STORAGE_KEY) === 'chamber' ? 'chamber' : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

/** Remembers the choice if storage allows; the theme still applies for this page if not. */
export function storeTheme(theme: Theme, storage: ThemeStorage | null = browserStorage()): void {
  try {
    storage?.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    // Storage is blocked or full: the choice lasts for this page only.
  }
}

/** Puts `theme` on the page: the attribute index.css reads, and the browser chrome colour. */
export function applyTheme(theme: Theme, root: HTMLElement = document.documentElement): void {
  root.dataset.theme = theme
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', THEME_COLOR[theme])
}

/** The theme on the page now (index.html's script set it from storage). */
export function currentTheme(root: HTMLElement = document.documentElement): Theme {
  return root.dataset.theme === 'chamber' ? 'chamber' : DEFAULT_THEME
}
