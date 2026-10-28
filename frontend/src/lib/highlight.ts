// The source reveal's highlight (M8 K6). The backend finds the question's
// grounding_reference in the passage with v1's grounding check and sends the span as
// UTF-16 offsets, which is how JavaScript indexes strings; this only cuts the text.
import type { Source } from '../api/types'

export type Highlighted = { before: string; marked: string; after: string }

/** The passage in three pieces around the highlight, or null when there is none or the
 * span doesn't fit the text (a mismatch is shown unmarked rather than mis-marked). */
export function splitHighlight(source: Pick<Source, 'text' | 'highlight'>): Highlighted | null {
  const { text, highlight } = source
  if (!highlight) return null
  const { start, end } = highlight
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > text.length) return null
  return { before: text.slice(0, start), marked: text.slice(start, end), after: text.slice(end) }
}
