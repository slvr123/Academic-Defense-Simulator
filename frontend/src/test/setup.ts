import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// jsdom has no matchMedia; GSAP's matchMedia (M10 N13) needs one. Nothing matches, so by
// default no animation runs and every screen renders in its final state; motion tests
// (src/motion/motion.test.tsx) install their own.
export function installMatchMedia(matches: (query: string) => boolean = () => false) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: (query: string) => ({
      matches: matches(query),
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}
installMatchMedia()

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  localStorage.clear() // saved answer drafts (M10 N8) belong to one test
  installMatchMedia()
})
