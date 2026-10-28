import { describe, expect, it } from 'vitest'
import { splitHighlight } from './highlight'
import { currentStep } from './turnSteps'

const text = 'Leaves were collected in May. Each leaf was photographed flat. Then sorted.'

describe('splitHighlight (M8 K6)', () => {
  it('cuts the passage around the span the backend sent', () => {
    const start = text.indexOf('Each')
    const end = text.indexOf(' Then')
    expect(splitHighlight({ text, highlight: { start, end } })).toEqual({
      before: 'Leaves were collected in May. ',
      marked: 'Each leaf was photographed flat.',
      after: ' Then sorted.',
    })
  })

  it('counts UTF-16 units, as the backend does, past a character outside the BMP', () => {
    const passage = 'Let \u{1D465} be the load. The server answered.'
    const start = passage.indexOf('The') // the math italic x is two units in JavaScript too
    expect(splitHighlight({ text: passage, highlight: { start, end: passage.length } })?.marked).toBe('The server answered.')
  })

  it.each([
    ['no highlight', null],
    ['an absent one', undefined],
    ['an empty span', { start: 4, end: 4 }],
    ['a reversed span', { start: 9, end: 4 }],
    ['past the end', { start: 4, end: text.length + 1 }],
    ['negative', { start: -1, end: 4 }],
    ['fractional', { start: 1.5, end: 4 }],
  ])('shows the passage unmarked for %s', (_name, highlight) => {
    expect(splitHighlight({ text, highlight })).toBeNull()
  })
})

describe('currentStep (M8 K6)', () => {
  it.each([
    [{ submitting: false, asking: true }, 'Asking'],
    [{ submitting: false, asking: false }, 'Your answer'],
    [{ submitting: true, asking: false }, 'Reviewing'],
    [{ submitting: true, asking: true }, 'Reviewing'],
  ] as const)('%o -> %s', (input, step) => {
    expect(currentStep(input)).toBe(step)
  })
})
