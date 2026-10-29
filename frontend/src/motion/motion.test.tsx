// M10 N13: GSAP runs only with no motion preference; with prefers-reduced-motion nothing
// is animated and every element is in its final state; unmounting reverts everything.
import { render } from '@testing-library/react'
import gsap from 'gsap'
import { describe, expect, it } from 'vitest'
import { PreparingScreen, QuestionScreen } from '../components/DefenseScreens'
import realOptions from '../test/panelOptions.real.json'
import { evaluation, nextQuestion, startResponse } from '../test/fixtures'
import { installMatchMedia, WIDE } from '../test/setup'
import { createPlayer } from '../voice/player'
import { MOTION_OK } from './motion'

const turn = { question: startResponse.question, answer: 'A', evaluation }

function renderQuestion() {
  return render(
    <QuestionScreen
      sessionId="abc"
      voiceChoice="none"
      player={createPlayer()}
      panel={startResponse.panel}
      question={nextQuestion.next_question ?? startResponse.question}
      draft=""
      last={turn}
      turns={[turn]}
      submitting={false}
      notice={null}
      onChange={() => {}}
      onSend={() => {}}
      onAutoRetry={() => {}}
      onOpenPage={null}
      documentPanel={null}
    />,
  )
}

const animated = (element: Element) => gsap.getTweensOf(element).length > 0

describe('motion (M10 N13)', () => {
  it('plays the handoff as one timeline: lit card, then question, then the source reveal', () => {
    installMatchMedia((query) => query === MOTION_OK || query === WIDE)
    const { unmount } = renderQuestion()
    const lit = document.querySelector('[data-lit]')
    const question = document.querySelector('[data-question]')
    const reveal = document.querySelector('[data-reveal]')
    for (const element of [lit, question, reveal]) expect(element && animated(element)).toBe(true)
    const [timeline] = new Set([lit, question, reveal].map((e) => e && gsap.getTweensOf(e)[0].parent))
    expect(timeline?.getChildren()).toHaveLength(3) // one timeline holds all three
    unmount()
    for (const element of [lit, question, reveal]) expect(element && animated(element)).toBe(false) // reverted on unmount
  })

  it('runs no GSAP animation under prefers-reduced-motion, so everything is in its final state', () => {
    installMatchMedia((query) => query === '(prefers-reduced-motion: reduce)' || query === WIDE)
    renderQuestion()
    const lit = document.querySelector('[data-lit]')
    const question = document.querySelector('[data-question]')
    const reveal = document.querySelector('[data-reveal]')
    for (const element of [lit, question, reveal]) {
      expect(element && animated(element)).toBe(false)
      expect((element as HTMLElement).style.opacity).toBe('')
      expect((element as HTMLElement).style.visibility).toBe('')
      expect((element as HTMLElement).style.transform).toBe('')
    }
  })

  it('staggers the preparing seats in, and not under reduced motion', () => {
    const seats = [...realOptions.rosters[0].archetypes.slice(0, 3), realOptions.devils_advocate]
    installMatchMedia((query) => query === MOTION_OK)
    const first = render(<PreparingScreen seats={seats} ingest={null} />)
    const items = Array.from(document.querySelectorAll('[data-seat]'))
    expect(items).toHaveLength(4)
    expect(items.every(animated)).toBe(true)
    first.unmount()

    installMatchMedia((query) => query === '(prefers-reduced-motion: reduce)')
    render(<PreparingScreen seats={seats} ingest={null} />)
    const still = Array.from(document.querySelectorAll<HTMLElement>('[data-seat]'))
    expect(still.some(animated)).toBe(false)
    expect(still.every((e) => e.style.opacity === '')).toBe(true)
  })
})
