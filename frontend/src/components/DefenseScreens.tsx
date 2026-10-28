// The answer loop (M8 K6, M10 N2): preparing, the question, the review, and the
// previous answer's source reveal. Scores stay sealed until the report (K7).
import { type ReactNode, useEffect, useId, useRef, useState } from 'react'
import { limits } from '../api/constraints.gen'
import type { ArchetypeOption, IngestSummary, PanelistOut, QuestionOut } from '../api/types'
import { useTypewriter } from '../hooks/useTypewriter'
import { loadAnswerDraft, saveAnswerDraft, wordCount } from '../lib/answerDrafts'
import { DEVILS_ADVOCATE_KEY } from '../lib/panel'
import { looksFor } from '../lib/panelCopy'
import { useHandoff, useSeatsArrive } from '../motion/motion'
import { pageSpan, panelistName } from '../lib/labels'
import { currentStep, TURN_STEPS, type TurnStep } from '../lib/turnSteps'
import type { Notice, Turn, VoiceChoice } from '../state/flow'
import type { Player } from '../voice/player'
import { type QuestionVoice, useQuestionVoice } from '../voice/useQuestionVoice'
import { Avatar } from './Avatar'
import { NoticeBanner } from './NoticeBanner'
import { type ActiveStatus, type Card, PanelGrid } from './PanelGrid'
import { SourcePassage } from './Sources'
import { TranscriptDrawer } from './TranscriptDrawer'
import { Button, Card as Surface, DocumentIcon, Eyebrow, Heading, Working } from './ui'

export const SEALED_TEXT = 'Answer recorded. Scores stay sealed until the debrief.'
export const GROUNDED_TEXT = 'Grounded in a passage from your document - revealed after you answer'
export const PREPARING_TITLE = 'The panel is reading your paper'

function cardsFromPanel(panel: PanelistOut[]): Card[] {
  return panel.map((p) => ({ archetypeKey: p.archetype_key, surname: p.name, title: p.title, icon: p.icon }))
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-xl border border-line-strong bg-field px-[18px] py-4">
      <span className="font-serif text-[28px] leading-none font-semibold">{value}</span>
      <span className="text-[13px] text-muted">{label}</span>
    </div>
  )
}

/** What ingest left out, from the session status (N6): real page ranges only. */
function Skipped({ ingest }: { ingest: IngestSummary }) {
  const lines: string[] = []
  for (const range of ingest.navigation_pages) lines.push(`Contents and list pages · ${pageSpan(range.start, range.end)}`)
  if (ingest.reference_list) lines.push(`Reference list · ${pageSpan(ingest.reference_list.start, ingest.reference_list.end)}`)
  if (ingest.literature_review && ingest.held_back_passages > 0) {
    const passages = `${ingest.held_back_passages} passage${ingest.held_back_passages === 1 ? '' : 's'}`
    lines.push(`Literature review · ${pageSpan(ingest.literature_review.start, ingest.literature_review.end)} · ${passages} held back in this version`)
  }
  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-line-strong bg-field px-[18px] py-4">
      <span className="text-[13px] font-semibold">Skipped, so no one asks about them</span>
      {lines.length === 0 ? (
        <span className="font-mono text-xs text-muted">No contents pages or reference list found</span>
      ) : (
        lines.map((line) => (
          <span key={line} className="font-mono text-xs text-muted">
            {line}
          </span>
        ))
      )}
    </div>
  )
}

/** "The panel is reading your paper" (N6, canvas Preparing.dc.html). Each seat shows its
 * role and what that role looks for; names arrive with /start's response. The first seat
 * writes question 1 (v1 opens with the first domain panelist); the Devil's Advocate speaks last. */
export function PreparingScreen({ seats, ingest }: { seats: ArchetypeOption[]; ingest: IngestSummary | null }) {
  const root = useRef<HTMLElement>(null)
  useSeatsArrive(root, seats.length)
  return (
    <section ref={root} aria-labelledby="preparing-title" className="flex flex-col gap-6 rounded-2xl border border-line-strong bg-card p-5 sm:gap-7 sm:px-10 sm:py-9">
      <div className="flex flex-col gap-2">
        <h2 id="preparing-title" className="flex items-center gap-3 font-serif text-[26px] font-semibold tracking-[-0.015em] sm:text-[32px]">
          <Working /> {PREPARING_TITLE}
        </h2>
        <p className="text-base text-muted">Here's what they have to work with. The first question is being written.</p>
      </div>

      {ingest && (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
          <Stat value={ingest.page_count} label={ingest.page_count === 1 ? 'page in your PDF' : 'pages in your PDF'} />
          <Stat value={ingest.citable_passages} label={ingest.citable_passages === 1 ? 'passage the panel can cite' : 'passages the panel can cite'} />
          <Skipped ingest={ingest} />
        </div>
      )}

      {seats.length > 0 && (
        <ul aria-label="Panel preparation" className="flex flex-col gap-2.5">
          {seats.map((seat, i) => {
            const first = i === 0
            const devil = seat.archetype_key === DEVILS_ADVOCATE_KEY
            const frame = first ? 'border-solid border-accent bg-accent-soft' : devil ? 'border-dashed border-devil-line bg-surface' : 'border-solid border-line-strong bg-surface'
            const status = first ? 'Writing question 1' : devil ? 'Speaks last' : 'Seated'
            return (
              <li key={seat.archetype_key} data-seat className={`flex flex-wrap items-center gap-x-3.5 gap-y-2 rounded-xl border px-4 py-3.5 ${frame}`}>
                <Avatar icon={seat.icon} size="md" dimmed={!first} />
                <span className="flex min-w-0 flex-[1_1_240px] flex-col gap-0.5">
                  <span className="font-semibold">{seat.title}</span>
                  <span className={`text-sm ${first ? 'text-ink' : 'text-muted'}`}>{looksFor(seat.archetype_key)}</span>
                </span>
                <span className={`flex shrink-0 items-center gap-2 font-mono text-xs ${first ? 'text-accent' : 'text-muted'}`}>
                  {first && <span aria-hidden className="size-[7px] animate-pulse rounded-full bg-accent motion-reduce:animate-none" />}
                  {status}
                </span>
              </li>
            )
          })}
        </ul>
      )}

      <p className="flex items-start gap-2 font-mono text-xs text-subtle">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="mt-px shrink-0">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 2" />
        </svg>
        Usually 30–60 seconds. Each panelist draws on the part of your paper their role cares about, and you see the exact passage after each answer.
      </p>
    </section>
  )
}

function Steps({ current }: { current: TurnStep }) {
  const at = TURN_STEPS.indexOf(current)
  return (
    <ol aria-label="Turn progress" className="flex flex-wrap gap-x-[18px] gap-y-1 font-mono text-xs">
      {TURN_STEPS.map((step, i) => {
        if (i < at) {
          return (
            <li key={step} className="flex items-center gap-1.5 text-muted">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M20 6 9 17l-5-5" />
              </svg>
              {step}
            </li>
          )
        }
        if (i === at) {
          return (
            <li key={step} aria-current="step" className="flex items-center gap-1.5 text-accent">
              <span aria-hidden className="size-[7px] rounded-full bg-accent" />
              {step === 'Reviewing' ? <span className="shimmer-text animate-shimmer motion-reduce:animate-none">{step}</span> : step}
            </li>
          )
        }
        return (
          <li key={step} className="text-faint">
            {step}
          </li>
        )
      })}
    </ol>
  )
}

/** Mounted once per question (keyed by turn), so the reveal restarts for each. */
function QuestionText({ text, onDone }: { text: string; onDone: (done: boolean) => void }) {
  const { shown, done, skip } = useTypewriter(text)
  useEffect(() => onDone(done), [done, onDone])
  return (
    <p className="max-w-[62ch] cursor-text font-serif text-[19px] leading-[1.5] text-ink sm:text-[26px] sm:leading-[1.45] sm:tracking-[-0.005em]" onClick={skip}>
      <span className="sr-only">{text}</span>
      <span aria-hidden>
        {shown}
        {!done && (
          <span className="ml-1 inline-block h-[1.05em] w-0.5 animate-caret bg-accent align-[-0.15em] motion-reduce:animate-none" />
        )}
      </span>
    </p>
  )
}

/** What the voice is doing, and the one control that fits: Replay, or Play when the browser blocked autoplay (E10). */
function VoiceControls({ voice, submitting }: { voice: QuestionVoice; submitting: boolean }) {
  switch (voice.status) {
    case 'off':
      return null
    case 'loading':
      return (
        <p className="flex items-center gap-2 font-mono text-xs text-muted">
          <Working /> Voice loading
        </p>
      )
    case 'blocked':
      return <Button onClick={voice.replay}>&#9654; Play the question</Button>
    case 'idle':
    case 'speaking':
      return (
        <div className="flex items-center gap-3">
          {/* "Speaking" shows on the panelist's card (N2). */}
          {!submitting && (
            <Button variant="secondary" onClick={voice.replay} className="min-h-10">
              &#9654; Replay
            </Button>
          )}
        </div>
      )
  }
}

/** The previous question's reveal, once its answer is recorded: the passage the panelist
 * was reading, with the words the question was grounded in highlighted. No scores (K7).
 * `onOpenPage` opens the candidate's own PDF at the passage's first page (N4). */
export function SourceReveal({ turn, onOpenPage }: { turn: Turn; onOpenPage: ((page: number) => void) | null }) {
  const { question, answer } = turn
  const asker = panelistName(question.panelist.name)
  const page = question.sources[0]?.page_start
  return (
    <aside data-reveal aria-label="Source passage" className="flex min-w-0 flex-col gap-4 rounded-2xl border border-accent bg-card p-5 sm:p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-serif text-xl font-semibold">What {asker} was reading</h3>
        <span className="font-mono text-xs text-muted">Question {question.turn}</span>
      </div>
      <p className="flex items-center gap-2 text-sm text-muted">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <rect x="5" y="11" width="14" height="10" rx="2" />
          <path d="M8 11V8a4 4 0 0 1 8 0v3" />
        </svg>
        {SEALED_TEXT}
      </p>
      {question.sources.map((source, i) => (
        <SourcePassage key={i} source={source} />
      ))}
      {onOpenPage && page !== undefined && (
        <Button variant="secondary" className="flex items-center gap-2 self-start" onClick={() => onOpenPage(page)}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M14 3h7v7" />
            <path d="M10 14 21 3" />
            <path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
          </svg>
          Open at page {page} in your PDF
        </Button>
      )}
      <details className="rounded-xl border border-line bg-field px-4 py-3 text-sm">
        <summary className="min-h-11 cursor-pointer content-center text-muted">The question and your answer</summary>
        <div className="space-y-3 pt-2 pb-1">
          <p className="font-serif text-base leading-relaxed">{question.question}</p>
          <p className="leading-relaxed whitespace-pre-line text-ink-dim">{answer}</p>
        </div>
      </details>
    </aside>
  )
}

/** Ctrl+Enter, or Cmd+Enter on a Mac (N8). */
function isSendShortcut(event: { key: string; ctrlKey: boolean; metaKey: boolean }): boolean {
  return event.key === 'Enter' && (event.ctrlKey || event.metaKey)
}

const SHORTCUT_HINT = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘+Enter to send' : 'Ctrl+Enter to send'

type QuestionScreenProps = {
  sessionId: string
  voiceChoice: VoiceChoice | null
  player: Player
  panel: PanelistOut[]
  question: QuestionOut
  draft: string
  last: Turn | null
  turns: Turn[]
  submitting: boolean
  notice: Notice | null
  onChange: (text: string) => void
  onSend: () => void
  onAutoRetry: () => void
  /** Opens the candidate's PDF at a page; null when the file isn't available (N4). */
  onOpenPage: ((page: number) => void) | null
  /** The open PDF panel, laid out beside the question (N4). */
  documentPanel: ReactNode
  /** The recorded demo (N5): the answer is read-only and the send button says "Next". */
  replay?: boolean
}

export function QuestionScreen(props: QuestionScreenProps) {
  const { sessionId, voiceChoice, player, panel, question, draft, last, turns, submitting, notice, onChange, onSend, onAutoRetry, onOpenPage, documentPanel } = props
  const { replay = false } = props
  const root = useRef<HTMLDivElement>(null)
  useHandoff(root, question.turn)
  const answerId = useId()
  const voice = useQuestionVoice({ player, choice: voiceChoice, sessionId, question, submitting })
  const [typed, setTyped] = useState(false)
  const [transcriptOpen, setTranscriptOpen] = useState(false)
  const counting = notice?.kind === 'busy' && notice.retryAt !== null
  const asker = panelistName(question.panelist.name)
  const asking = !typed || voice.status === 'loading' || voice.status === 'speaking'
  const step = currentStep({ submitting, asking })
  const canSend = draft.trim() !== '' && !counting && !submitting
  const status: ActiveStatus = submitting ? 'reviewing' : voice.status === 'speaking' ? 'speaking' : 'asking'
  const asked: Record<string, number> = {}
  for (const turn of turns) asked[turn.question.panelist.archetype_key] = (asked[turn.question.panelist.archetype_key] ?? 0) + 1

  // N8: a draft saved for this question comes back, once, when the question opens.
  const restored = useRef<string | null>(null)
  useEffect(() => {
    const key = `${sessionId}:${question.turn}`
    if (replay || restored.current === key) return
    restored.current = key
    const saved = loadAnswerDraft(sessionId, question.turn)
    if (saved !== '' && draft === '') onChange(saved)
  }, [replay, sessionId, question.turn, draft, onChange])

  const edit = (text: string) => {
    saveAnswerDraft(sessionId, question.turn, text)
    onChange(text)
  }

  const reveal = last && <SourceReveal key={last.question.turn} turn={last} onOpenPage={onOpenPage} />

  return (
    <div ref={root} className="space-y-5">
      <PanelGrid cards={cardsFromPanel(panel)} active={question.panelist.archetype_key} status={status} asked={asked} />
      <div className="flex flex-wrap items-start gap-6">
        <div className={`flex min-w-0 flex-col gap-6 ${documentPanel ? 'flex-[999_1_560px]' : 'flex-[1_1_520px]'}`}>
          <section
            data-question
            aria-label={`Question ${question.turn}`}
            className="flex min-w-0 flex-col gap-6 rounded-2xl border border-line-strong bg-card p-5 sm:gap-7 sm:px-9 sm:pt-8 sm:pb-7"
          >
            <div className="flex flex-wrap items-center justify-between gap-4">
              <Eyebrow className="text-accent">
                {asker} {submitting ? 'asked' : 'asks'}
                <span className="text-muted normal-case"> · {question.panelist.title}</span>
              </Eyebrow>
              <Steps current={step} />
            </div>

            {submitting ? (
              <>
                <p className="font-serif text-[19px] leading-normal sm:text-[21px]">{question.question}</p>
                <div className="flex flex-col gap-2 rounded-xl border border-line bg-field px-5 py-4">
                  <span className="font-mono text-xs text-subtle">Your answer</span>
                  <p className="text-base leading-relaxed whitespace-pre-line text-ink-dim">{draft}</p>
                </div>
                <p className="flex items-center gap-2 text-sm text-muted">
                  <Working /> {asker} is reviewing your answer.
                </p>
              </>
            ) : (
              <>
                <QuestionText key={question.turn} text={question.question} onDone={setTyped} />
                <p className="flex items-center gap-2 font-mono text-xs text-subtle">
                  <DocumentIcon /> {GROUNDED_TEXT}
                </p>
                <VoiceControls voice={voice} submitting={submitting} />
                <form
                  className="flex flex-col gap-2.5 border-t border-line pt-4"
                  onSubmit={(event) => {
                    event.preventDefault()
                    onSend()
                  }}
                >
                  <label htmlFor={answerId} className="text-sm font-medium text-muted">
                    Your answer
                  </label>
                  <textarea
                    id={answerId}
                    rows={6}
                    value={draft}
                    readOnly={replay}
                    placeholder={replay ? undefined : 'Answer as you would in the room.'}
                    maxLength={limits.AnswerRequest.text.maxLength}
                    onChange={(event) => edit(event.target.value)}
                    onKeyDown={(event) => {
                      if (!isSendShortcut(event)) return
                      event.preventDefault()
                      if (canSend) onSend()
                    }}
                    className="w-full resize-y rounded-xl border border-line-strong bg-field p-4 text-base leading-relaxed text-ink placeholder:text-subtle focus:border-accent focus:outline-none"
                  />
                  {notice && (
                    <NoticeBanner notice={notice} onRetry={onSend} onAutoRetry={onAutoRetry} retryLabel={notice.kind === 'turnFailed' ? 'Send it again' : 'Try again'} />
                  )}
                  <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
                    {turns.length > 0 ? (
                      <button
                        type="button"
                        onClick={() => setTranscriptOpen(true)}
                        className="flex min-h-11 items-center gap-2 rounded-lg px-1 text-sm text-muted transition-colors hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent motion-reduce:transition-none"
                      >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden>
                          <path d="M4 6h16M4 12h16M4 18h10" />
                        </svg>
                        Transcript · {turns.length} earlier {turns.length === 1 ? 'turn' : 'turns'}
                      </button>
                    ) : (
                      <span className="max-sm:hidden" />
                    )}
                    {!notice && (
                      // On a phone the send bar stays at the bottom of the screen (bench-mobile).
                      <div className="sticky bottom-0 -mx-5 flex items-center justify-between gap-4 border-t border-line bg-ground px-4 pt-3 pb-6 sm:static sm:mx-0 sm:justify-end sm:border-0 sm:bg-transparent sm:p-0">
                        <span className="font-mono text-xs text-subtle" aria-live="off">
                          {wordCount(draft)} {wordCount(draft) === 1 ? 'word' : 'words'}
                          <span className="max-sm:hidden"> · {SHORTCUT_HINT}</span>
                        </span>
                        <Button type="submit" disabled={!canSend} className="min-h-12 sm:px-7">
                          {replay ? 'Next' : 'Submit answer'}
                        </Button>
                      </div>
                    )}
                  </div>
                </form>
              </>
            )}
          </section>
          {documentPanel && reveal}
        </div>
        {documentPanel || (reveal && <div className="flex min-w-0 flex-[1_1_420px]">{reveal}</div>)}
      </div>
      {transcriptOpen && <TranscriptDrawer turns={turns} onClose={() => setTranscriptOpen(false)} />}
    </div>
  )
}

export function FinishingScreen({ notice, onRetry }: { notice: Notice | null; onRetry: () => void }) {
  return (
    <Surface className="space-y-3">
      <Heading>
        <Working /> The panel is writing its report
      </Heading>
      <p className="text-sm text-muted">Your last answer is recorded. Putting the debrief together.</p>
      {notice && <NoticeBanner notice={notice} onRetry={onRetry} />}
    </Surface>
  )
}
