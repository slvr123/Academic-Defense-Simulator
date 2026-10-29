// The previous question's reveal, once its answer is recorded: the passage the panelist
// was reading, with the words the question was grounded in highlighted. No scores (K7).
//
// It never makes the page taller: the card has a fixed height (the question column's at
// wide widths, 360 px below them), nothing in it scrolls, and what doesn't fit goes onto
// further pages, broken only between sentences. Page 1 is the passage with "Open at page
// N in your PDF"; then the passage's continuation, if any; then the question and the
// answer. Pages turn with ‹ ›, a swipe, or the arrow keys while the card has focus; the
// turn is a plain CSS transition, none under reduced motion. Each new reveal (keyed by
// turn) starts on page 1.
import { type KeyboardEvent, type PointerEvent, type ReactNode, useLayoutEffect, useRef, useState } from 'react'
import type { Source } from '../api/types'
import { panelistName } from '../lib/labels'
import { paginate, sentenceSpans, type Span, wordSpans } from '../lib/paging'
import type { Turn } from '../state/flow'
import { SourcePassage } from './Sources'
import { Button } from './ui'

export const SEALED_TEXT = 'Answer recorded. Scores stay sealed until the debrief.'

type Page = { kind: 'passage'; source: number; span: Span } | { kind: 'qa'; span: Span }

const SWIPE_PX = 40
const GAP_PX = 12 // gap-3, between the paper card and the PDF button

/** Before anything is measured (or where layout isn't real, as in jsdom): a page per part. */
function wholePages(turn: Turn): Page[] {
  return [
    ...turn.question.sources.map((source, i): Page => ({ kind: 'passage', source: i, span: { start: 0, end: source.text.length } })),
    { kind: 'qa', span: { start: 0, end: turn.answer.length } },
  ]
}

export function SourceReveal({ turn, onOpenPage }: { turn: Turn; onOpenPage: ((page: number) => void) | null }) {
  const { question, answer } = turn
  const asker = panelistName(question.panelist.name)
  const firstPage = question.sources[0]?.page_start
  const [pages, setPages] = useState<Page[]>(() => wholePages(turn))
  const [at, setAt] = useState(0)
  const body = useRef<HTMLDivElement>(null)
  const measure = useRef<HTMLDivElement>(null)
  const swipeFrom = useRef<number | null>(null)
  const current = Math.min(at, pages.length - 1)

  // Lay the text out on pages that fit the body, again whenever the body's size changes.
  useLayoutEffect(() => {
    const element = body.current
    const meter = measure.current
    if (element === null || meter === null) return
    const layout = () => {
      const height = element.clientHeight
      if (height === 0) return // not laid out (jsdom, or hidden): keep a page per part
      setPages(measuredPages(turn, meter, height, onOpenPage !== null))
    }
    layout()
    if (typeof ResizeObserver !== 'function') return
    const observer = new ResizeObserver(layout)
    observer.observe(element)
    return () => observer.disconnect()
  }, [turn, onOpenPage])

  const go = (to: number) => setAt(Math.max(0, Math.min(pages.length - 1, to)))
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'ArrowRight') go(current + 1)
    else if (event.key === 'ArrowLeft') go(current - 1)
    else return
    event.preventDefault()
  }
  const onPointerDown = (event: PointerEvent) => {
    swipeFrom.current = event.pointerType === 'mouse' ? null : event.clientX
  }
  const onPointerUp = (event: PointerEvent) => {
    if (swipeFrom.current === null) return
    const dx = event.clientX - swipeFrom.current
    swipeFrom.current = null
    if (Math.abs(dx) >= SWIPE_PX) go(current + (dx < 0 ? 1 : -1))
  }

  const openButton = onOpenPage && firstPage !== undefined && (
    <Button variant="secondary" className="flex shrink-0 items-center gap-2 self-start" onClick={() => onOpenPage(firstPage)}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M14 3h7v7" />
        <path d="M10 14 21 3" />
        <path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
      </svg>
      Open at page {firstPage} in your PDF
    </Button>
  )

  return (
    <aside
      data-reveal
      aria-label="Source passage"
      aria-roledescription="carousel"
      tabIndex={0}
      onKeyDown={onKeyDown}
      className="flex h-[360px] min-w-0 flex-col gap-3 overflow-hidden rounded-2xl border border-accent bg-card p-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:p-5 lg:absolute lg:inset-0 lg:h-auto"
    >
      {/* Compact on phones, where the card is 360 px and the passage needs the room. */}
      <div className="flex shrink-0 flex-col gap-1 sm:gap-1.5">
        <div className="flex items-baseline justify-between gap-x-2">
          <h3 className="min-w-0 truncate font-serif text-base font-semibold sm:text-xl">What {asker} was reading</h3>
          <span className="shrink-0 font-mono text-xs text-muted">Question {question.turn}</span>
        </div>
        <p className="flex items-center gap-2 text-xs text-muted sm:text-sm">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="shrink-0">
            <rect x="5" y="11" width="14" height="10" rx="2" />
            <path d="M8 11V8a4 4 0 0 1 8 0v3" />
          </svg>
          {SEALED_TEXT}
        </p>
      </div>

      <div ref={body} className="relative min-h-0 flex-1 touch-pan-y overflow-hidden" onPointerDown={onPointerDown} onPointerUp={onPointerUp}>
        <div
          className="flex h-full transition-transform duration-300 ease-out motion-reduce:transition-none"
          style={{ transform: `translateX(-${current * 100}%)` }}
        >
          {pages.map((page, i) => (
            <div
              key={i}
              role="group"
              aria-roledescription="page"
              aria-label={`${i + 1} of ${pages.length}`}
              inert={i !== current}
              className="flex h-full w-full shrink-0 flex-col gap-3 overflow-hidden"
            >
              {page.kind === 'passage' ? (
                <>
                  <SourcePassage source={question.sources[page.source]} span={page.span} compact />
                  {i === 0 && openButton}
                </>
              ) : (
                <QuestionAndAnswer question={page.span.start === 0 ? question.question : null} answer={answer.slice(page.span.start, page.span.end)} />
              )}
            </div>
          ))}
        </div>
        {/* What the pages are measured against: the same blocks, never shown. */}
        <div ref={measure} aria-hidden className="pointer-events-none invisible absolute inset-x-0 top-0">
          {question.sources.map((source, i) => (
            <div key={i} data-measure-source={i}>
              <SourcePassage source={{ ...source, text: '', highlight: null }} compact />
            </div>
          ))}
          <div data-measure-button>{openButton}</div>
          <div data-measure-qa>
            <QuestionAndAnswer question={question.question} answer="" />
          </div>
          <div data-measure-qa-more>
            <QuestionAndAnswer question={null} answer="" />
          </div>
        </div>
      </div>

      <div className="flex shrink-0 items-center justify-between gap-3">
        <PageButton label="Previous page" disabled={current === 0} onClick={() => go(current - 1)}>
          ‹
        </PageButton>
        <span className="font-mono text-xs text-muted" aria-live="polite">
          {current + 1} / {pages.length}
        </span>
        <PageButton label="Next page" disabled={current === pages.length - 1} onClick={() => go(current + 1)}>
          ›
        </PageButton>
      </div>
    </aside>
  )
}

function QuestionAndAnswer({ question, answer }: { question: string | null; answer: string }) {
  return (
    <div className="flex flex-col gap-2.5 text-sm">
      {question !== null && (
        <>
          <p className="font-mono text-xs text-subtle">The question</p>
          <p className="font-serif text-base leading-relaxed">{question}</p>
        </>
      )}
      <p className="font-mono text-xs text-subtle">{question === null ? 'Your answer, continued' : 'Your answer'}</p>
      <p data-answer className="leading-relaxed whitespace-pre-line text-ink-dim">
        {answer}
      </p>
    </div>
  )
}

function PageButton({ label, disabled, onClick, children }: { label: string; disabled: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="flex size-11 items-center justify-center rounded-lg border border-line-strong text-lg text-ink transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:border-line disabled:text-faint motion-reduce:transition-none"
    >
      {children}
    </button>
  )
}

/** Pages that fit `height`, measured on the hidden copies inside `meter`. */
function measuredPages(turn: Turn, meter: HTMLElement, height: number, hasButton: boolean): Page[] {
  const buttonHeight = hasButton ? (meter.querySelector<HTMLElement>('[data-measure-button]')?.offsetHeight ?? 0) + GAP_PX : 0
  const pages: Page[] = []
  turn.question.sources.forEach((source: Source, i) => {
    const holder = meter.querySelector<HTMLElement>(`[data-measure-source="${i}"]`)
    const quote = holder?.querySelector('blockquote')
    if (!holder || !quote) return
    const fits = (span: Span, page: number) => {
      quote.textContent = source.text.slice(span.start, span.end)
      const first = pages.length === 0 && page === 0
      return holder.offsetHeight + (first ? buttonHeight : 0) <= height
    }
    for (const span of paginate(sentenceSpans(source.text), fits, (unit) => wordSpans(source.text, unit))) {
      pages.push({ kind: 'passage', source: i, span })
    }
    quote.textContent = ''
  })
  const answer = turn.answer
  const opening = meter.querySelector<HTMLElement>('[data-measure-qa]')
  const more = meter.querySelector<HTMLElement>('[data-measure-qa-more]')
  if (opening && more) {
    const fits = (span: Span, page: number) => {
      const holder = page === 0 ? opening : more
      const text = holder.querySelector('[data-answer]')
      if (!text) return true
      text.textContent = answer.slice(span.start, span.end)
      const fit = holder.offsetHeight <= height
      text.textContent = ''
      return fit
    }
    const spans = answer === '' ? [{ start: 0, end: 0 }] : paginate(sentenceSpans(answer), fits, (unit) => wordSpans(answer, unit))
    for (const span of spans) pages.push({ kind: 'qa', span })
  }
  return pages
}
