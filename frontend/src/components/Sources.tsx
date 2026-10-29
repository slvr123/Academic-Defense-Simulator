// The passage a question was grounded in, printed on a "paper" card (M8 K6) with the
// question's grounding_reference highlighted. Shown only once that question's answer is
// recorded, never beside the open question: it would hand over the answer (D18).
import type { Source } from '../api/types'
import { splitHighlight } from '../lib/highlight'
import { pageRange, SOURCE_ORIGIN_LABELS } from '../lib/labels'
import { highlightWithin, type Span } from '../lib/paging'

/** The passage on its paper card. With `span`, only that part of it: one page of the
 * paged reveal, highlight included where it falls on the page. `compact` is the reveal's
 * tighter card, which shares a fixed height with the rest of the page. */
export function SourcePassage({ source, span, compact = false }: { source: Source; span?: Span; compact?: boolean }) {
  const whole = { start: 0, end: source.text.length }
  const part = span ?? whole
  const valid = splitHighlight(source) !== null && source.highlight ? { start: source.highlight.start, end: source.highlight.end } : null
  const marked = highlightWithin(valid, part)
  const text = source.text.slice(part.start, part.end)
  const continued = part.start > 0
  const pad = compact ? 'px-4 py-4 sm:px-5' : 'px-5 py-5 sm:px-7 sm:py-7'
  return (
    <figure className={`rounded-md bg-paper text-paper-ink shadow-[0_1px_0_var(--color-paper-edge),0_18px_40px_rgba(0,0,0,0.35)] ${pad}`}>
      <figcaption className={`flex flex-wrap justify-between gap-x-3 gap-y-1 font-mono text-[11px] text-paper-muted ${compact ? 'mb-2.5' : 'mb-3.5'}`}>
        <span>
          {pageRange(source.page_start, source.page_end)}
          {continued && ' · continued'}
        </span>
        <span>
          {SOURCE_ORIGIN_LABELS[source.origin]} · match {source.score.toFixed(2)}
        </span>
      </figcaption>
      <blockquote className={`font-serif whitespace-pre-line ${compact ? 'text-sm leading-[1.55] sm:text-[15px] sm:leading-[1.6]' : 'text-base leading-[1.65]'}`}>
        {marked ? (
          <>
            {text.slice(0, marked.start)}
            <mark className="rounded-[2px] bg-highlight px-0.5 text-paper-ink [box-decoration-break:clone]">{text.slice(marked.start, marked.end)}</mark>
            {text.slice(marked.end)}
          </>
        ) : (
          text
        )}
      </blockquote>
    </figure>
  )
}

export function Sources({ sources }: { sources: Source[] }) {
  return (
    <div className="space-y-3">
      {sources.map((source, i) => (
        <SourcePassage key={i} source={source} />
      ))}
    </div>
  )
}
