import { describe, expect, it } from 'vitest'
import { highlightWithin, paginate, sentenceSpans, wordSpans } from './paging'

const text = 'First one. Second, longer sentence here! Third? Last line'
const slice = (s: { start: number; end: number }) => text.slice(s.start, s.end)

describe('reveal paging', () => {
  it('splits into sentences that tile the text', () => {
    const spans = sentenceSpans(text)
    expect(spans.map(slice)).toEqual(['First one. ', 'Second, longer sentence here! ', 'Third? ', 'Last line'])
    expect(spans.map(slice).join('')).toBe(text)
  })

  it('breaks at blank lines, and keeps decimals and closing quotes inside a sentence', () => {
    expect(sentenceSpans('Recall was 0.813 here. Next').map((s) => s.end)).toEqual([23, 27])
    expect(sentenceSpans('One\n\nTwo').length).toBe(2)
    expect(sentenceSpans('He said "stop." Then left').length).toBe(2)
  })

  it('fills pages greedily, breaking only between sentences', () => {
    const units = sentenceSpans(text)
    const pages = paginate(units, (span) => span.end - span.start <= 31)
    expect(pages.map(slice)).toEqual(['First one. ', 'Second, longer sentence here! ', 'Third? Last line'])
  })

  it('lets the first page hold less than the rest', () => {
    const pages = paginate(sentenceSpans(text), (span, page) => span.end - span.start <= (page === 0 ? 11 : 60))
    expect(pages.map(slice)).toEqual(['First one. ', 'Second, longer sentence here! Third? Last line'])
  })

  it('splits a sentence into words only when it fits no page alone', () => {
    const long = { start: 11, end: 41 } // "Second, longer sentence here! "
    const pages = paginate([long], (span) => span.end - span.start <= 16, (unit) => wordSpans(text, unit))
    expect(pages.map(slice)).toEqual(['Second, longer ', 'sentence here! '])
  })

  it('keeps each page its own part of the highlight', () => {
    expect(highlightWithin({ start: 5, end: 15 }, { start: 0, end: 11 })).toEqual({ start: 5, end: 11 })
    expect(highlightWithin({ start: 5, end: 15 }, { start: 11, end: 41 })).toEqual({ start: 0, end: 4 })
    expect(highlightWithin({ start: 5, end: 15 }, { start: 41, end: 48 })).toBeNull()
    expect(highlightWithin(null, { start: 0, end: 5 })).toBeNull()
  })
})
