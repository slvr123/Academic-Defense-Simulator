// Paging the source reveal (M10 follow-up): the card has a fixed height and nothing in it
// scrolls, so long text goes onto further pages, broken only between sentences. These are
// the pure parts; RevealCard measures what fits.

export type Span = { start: number; end: number } // [start, end) in UTF-16 code units

/** Sentence spans covering all of `text`: a break after . ! or ? followed by whitespace, or
 * at a blank line. Each span keeps its trailing whitespace, so the spans tile the text. */
export function sentenceSpans(text: string): Span[] {
  const spans: Span[] = []
  const boundary = /[.!?]["')\]]*\s+|\n\s*\n\s*/g
  let start = 0
  for (const match of text.matchAll(boundary)) {
    const end = (match.index ?? 0) + match[0].length
    if (end > start && end < text.length) {
      spans.push({ start, end })
      start = end
    }
  }
  if (start < text.length || spans.length === 0) spans.push({ start, end: text.length })
  return spans
}

/** Word spans inside one span, for a single sentence too long for a page. */
export function wordSpans(text: string, span: Span): Span[] {
  const words: Span[] = []
  const pattern = /\S+\s*/g
  pattern.lastIndex = span.start
  for (let match = pattern.exec(text); match !== null && match.index < span.end; match = pattern.exec(text)) {
    words.push({ start: match.index, end: Math.min(span.end, match.index + match[0].length) })
  }
  return words.length > 0 ? words : [span]
}

/**
 * Greedy pages over `units` (contiguous spans): each page takes as many units as `fits`
 * allows for [its first start, the candidate's end). `pageIndex` is the page being filled,
 * since the first page can hold less (it has the PDF button). A unit that fits on no page
 * alone is split into words (`split`), so text is never hidden; that's the only break
 * inside a sentence.
 */
export function paginate(
  units: Span[],
  fits: (span: Span, pageIndex: number) => boolean,
  split: (unit: Span) => Span[] = (unit) => [unit],
): Span[] {
  const pages: Span[] = []
  const queue = [...units]
  while (queue.length > 0) {
    const page = pages.length
    const first = queue[0]
    if (!fits(first, page)) {
      const words = split(first)
      if (words.length > 1) {
        queue.splice(0, 1, ...words)
        continue
      }
    }
    let end = 1
    while (end < queue.length && fits({ start: first.start, end: queue[end].end }, page)) end++
    pages.push({ start: first.start, end: queue[end - 1].end })
    queue.splice(0, end)
  }
  return pages
}

/** The highlight's part inside one page's span, as offsets into that page's slice. */
export function highlightWithin(highlight: Span | null, page: Span): Span | null {
  if (highlight === null) return null
  const start = Math.max(highlight.start, page.start)
  const end = Math.min(highlight.end, page.end)
  return end > start ? { start: start - page.start, end: end - page.start } : null
}
