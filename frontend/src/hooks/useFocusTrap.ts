import { type RefObject, useEffect } from 'react'

const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), iframe, [tabindex]:not([tabindex="-1"])'

/**
 * For a modal surface (a drawer, a confirm, the phone PDF sheet): focus moves into
 * `container` when it opens, Tab and Shift+Tab cycle inside it, Esc calls `onClose`, and
 * focus goes back to whatever had it before when it closes (M10 N10).
 */
export function useFocusTrap(container: RefObject<HTMLElement | null>, onClose: () => void, active = true): void {
  useEffect(() => {
    const root = container.current
    if (!active || root === null) return
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const focusables = () => Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE))
    ;(focusables()[0] ?? root).focus()

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusables()
      if (items.length === 0) {
        event.preventDefault()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      before?.focus()
    }
  }, [container, onClose, active])
}
