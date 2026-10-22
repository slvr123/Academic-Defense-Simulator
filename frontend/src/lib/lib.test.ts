import { describe, expect, it } from 'vitest'
import { afterSeconds, formatManila, moreAt } from './manilaTime'
import { toHundred, turnOverall } from './scoreDisplay'
import { isTooLarge, MAX_UPLOAD_BYTES } from './upload'

describe('toHundred, v1.2.1 Decision 4', () => {
  // The cases v1 pins in tests/test_report.py.
  it.each([
    [1, 20],
    [3, 60],
    [3.4, 68],
    [5, 100],
  ])('%s -> %s', (score, expected) => {
    expect(toHundred(score)).toBe(expected)
  })

  it('rounds a tie up, as v1 does, not to even: 1.025 -> 20.5 -> 21', () => {
    expect(toHundred(1.025)).toBe(21)
  })

  it('averages a turn on 1-5 before rescaling', () => {
    expect(turnOverall(11)).toBe(73) // 11 / 3 = 3.667 -> 73.3 -> 73
    expect(turnOverall(15)).toBe(100)
    expect(turnOverall(3)).toBe(20)
  })
})

describe('Manila time', () => {
  it('formats midnight Pacific as the afternoon in Manila', () => {
    expect(formatManila(new Date('2026-10-23T00:00:00-07:00'))).toBe('Fri 3:00 PM')
    // After the US clocks change, midnight Pacific is an hour later in Manila.
    expect(formatManila(new Date('2026-11-10T00:00:00-08:00'))).toBe('Tue 4:00 PM')
  })

  it('adds Retry-After seconds to now', () => {
    const now = new Date('2026-10-22T05:00:00Z')
    expect(afterSeconds(now, 3600).toISOString()).toBe('2026-10-22T06:00:00.000Z')
  })

  it('writes the "More at" line', () => {
    expect(moreAt(new Date('2026-10-23T07:00:00Z'))).toBe('More at Fri 3:00 PM, Manila time.')
  })
})

describe('the 30 MB pre-check', () => {
  const fileOf = (size: number) => {
    const file = new File([], 'thesis.pdf')
    Object.defineProperty(file, 'size', { value: size })
    return file
  }

  it('lets exactly 30 MB through and stops one byte more, as the server does', () => {
    expect(MAX_UPLOAD_BYTES).toBe(31_457_280)
    expect(isTooLarge(fileOf(MAX_UPLOAD_BYTES))).toBe(false)
    expect(isTooLarge(fileOf(MAX_UPLOAD_BYTES + 1))).toBe(true)
  })
})
