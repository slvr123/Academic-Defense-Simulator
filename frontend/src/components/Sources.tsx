// The passage a question was grounded in, printed on a "paper" card (M8 K6) with the
// question's grounding_reference highlighted. Shown only once that question's answer is
// recorded, never beside the open question: it would hand over the answer (D18).
import type { Source } from '../api/types'
import { splitHighlight } from '../lib/highlight'
import { pageRange, SOURCE_ORIGIN_LABELS } from '../lib/labels'

export function SourcePassage({ source }: { source: Source }) {
  const parts = splitHighlight(source)
  return (
    <figure className="rounded-md bg-paper px-5 py-5 text-paper-ink shadow-[0_1px_0_var(--color-paper-edge),0_18px_40px_rgba(0,0,0,0.35)] sm:px-7 sm:py-7">
      <figcaption className="mb-3.5 flex flex-wrap justify-between gap-x-3 gap-y-1 font-mono text-[11px] text-paper-muted">
        <span>{pageRange(source.page_start, source.page_end)}</span>
        <span>
          {SOURCE_ORIGIN_LABELS[source.origin]} · match {source.score.toFixed(2)}
        </span>
      </figcaption>
      <blockquote className="font-serif text-base leading-[1.65] whitespace-pre-line">
        {parts ? (
          <>
            {parts.before}
            <mark className="rounded-[2px] bg-highlight px-0.5 text-paper-ink [box-decoration-break:clone]">{parts.marked}</mark>
            {parts.after}
          </>
        ) : (
          source.text
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
