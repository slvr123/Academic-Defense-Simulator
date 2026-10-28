// Earlier turns (M10 N10): each question, the answer and the source pages. No scores:
// they stay sealed until the debrief (M8 K7). Focus is trapped while it's open; Esc closes.
import { useId, useRef } from 'react'
import { useFocusTrap } from '../hooks/useFocusTrap'
import { pageRange, panelistName } from '../lib/labels'
import type { Turn } from '../state/flow'

export function TranscriptDrawer({ turns, onClose }: { turns: Turn[]; onClose: () => void }) {
  const root = useRef<HTMLDivElement>(null)
  const titleId = useId()
  useFocusTrap(root, onClose)
  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-ground/70" onClick={onClose}>
      <div
        ref={root}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
        className="flex h-full w-full max-w-[520px] flex-col border-l border-line-strong bg-card"
      >
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
          <h2 id={titleId} className="font-serif text-xl font-semibold">
            Transcript
          </h2>
          <button
            type="button"
            aria-label="Close transcript"
            onClick={onClose}
            className="flex size-11 items-center justify-center rounded-lg text-muted transition-colors hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent motion-reduce:transition-none"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>
        <ol className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
          {turns.map(({ question, answer }) => (
            <li key={question.turn} className="space-y-2">
              <p className="font-mono text-xs text-muted">
                Question {question.turn} · {panelistName(question.panelist.name)} · {question.panelist.title}
              </p>
              <p className="font-serif leading-relaxed">{question.question}</p>
              <blockquote className="border-l-2 border-line-strong pl-3 text-sm whitespace-pre-line text-ink-dim">{answer}</blockquote>
              <p className="font-mono text-xs text-subtle">
                {question.sources.map((s) => pageRange(s.page_start, s.page_end)).join(' · ')}
              </p>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}
