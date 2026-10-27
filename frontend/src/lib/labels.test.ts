import { describe, expect, it } from 'vitest'
import { pageRange } from './labels'

describe('pageRange (M7 J6)', () => {
  it('names PDF pages, since printed page numbers differ', () => {
    expect(pageRange(77, 77)).toBe('PDF page 77')
    expect(pageRange(16, 18)).toBe('PDF pages 16–18')
  })
})
