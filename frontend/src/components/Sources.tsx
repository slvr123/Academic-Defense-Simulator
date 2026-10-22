// The passage a question was grounded in. Shown only once that question is answered,
// never beside the open question: it would hand over the answer (D18).
import type { Source } from '../api/types'
import { pageRange, SOURCE_ORIGIN_LABELS } from '../lib/labels'

export function Sources({ sources }: { sources: Source[] }) {
  return (
    <div className="space-y-2">
      {sources.map((source, i) => (
        <figure key={i} className="rounded-lg border border-line bg-paper p-3 text-sm">
          <figcaption className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
            <span className="font-medium text-ink">{pageRange(source.page_start, source.page_end)}</span>
            <span>{SOURCE_ORIGIN_LABELS[source.origin]}</span>
            <span>match {source.score.toFixed(2)}</span>
          </figcaption>
          <blockquote className="mt-2 whitespace-pre-line font-serif leading-relaxed">{source.text}</blockquote>
        </figure>
      ))}
    </div>
  )
}
