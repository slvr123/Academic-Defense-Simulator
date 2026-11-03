// M12 D3 and D4 through the real App: the header's theme toggle and its persistence, and
// no glow on the lit (speaking) panel card.
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PanelGrid } from './components/PanelGrid'
import { THEME_STORAGE_KEY } from './lib/theme'
import { defaultServer, renderApp } from './test/renderApp'

vi.setConfig({ testTimeout: 15_000 })

const toggle = () => screen.getByRole('button', { name: 'Dark theme' })

describe('the theme toggle (M12 D4)', () => {
  it('starts on Paper, switches to Chamber and back, and remembers the choice', async () => {
    defaultServer()
    const user = renderApp()
    expect(document.documentElement.dataset.theme).toBeUndefined() // :root is Paper
    expect(toggle()).toHaveAttribute('aria-pressed', 'false')

    await user.click(toggle())
    expect(document.documentElement.dataset.theme).toBe('chamber')
    expect(toggle()).toHaveAttribute('aria-pressed', 'true')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('chamber')

    await user.click(toggle())
    expect(document.documentElement.dataset.theme).toBe('paper')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('paper')
  })

  it('reads the theme index.html put on <html> before first paint', () => {
    document.documentElement.dataset.theme = 'chamber'
    defaultServer()
    renderApp()
    expect(toggle()).toHaveAttribute('aria-pressed', 'true')
  })

  it('still switches when localStorage throws, for this page only', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    defaultServer()
    const user = renderApp()
    await user.click(toggle())
    expect(document.documentElement.dataset.theme).toBe('chamber')
    expect(setItem).toHaveBeenCalled()
    setItem.mockRestore()
  })
})

// Class names, since jsdom computes no Tailwind styles; scripts/check_no_glow.py checks the
// computed style of the same card in a real browser.
const GLOW = /shadow|ring-|glow|blur|drop-shadow|animate-speaking/

describe('the speaking card (M12 D3)', () => {
  it('is lit by a 1px solid accent border and carries no glow class', () => {
    render(
      <PanelGrid
        cards={[
          { archetypeKey: 'methodology_expert', surname: 'Reyes', title: 'Methodology Expert', icon: 'advisor' },
          { archetypeKey: 'devils_advocate', surname: 'Cruz', title: 'Terror Panelist', icon: 'hacker' },
        ]}
        active="methodology_expert"
        status="speaking"
        asked={{}}
      />,
    )
    const card = screen.getByText('Speaking').closest('li')
    expect(card).not.toBeNull()
    const lit = card as HTMLElement
    expect(lit).toHaveAttribute('data-lit')
    expect(lit.className.split(/\s+/)).toEqual(expect.arrayContaining(['border', 'border-solid', 'border-accent']))
    for (const element of [lit, ...Array.from(lit.querySelectorAll('*'))]) {
      expect(element.getAttribute('class') ?? '').not.toMatch(GLOW)
    }
    expect(lit.querySelector('img')).toHaveAttribute('data-speaking')
  })
})
