// The answer loop on the bench (M8 K6): preparing, the question, the review, and the
// previous answer's source reveal. Scores stay sealed until the report (K7).
import { useEffect, useId, useState } from 'react'
import { limits } from '../api/constraints.gen'
import type { ArchetypeOption, PanelistOut, QuestionOut } from '../api/types'
import { useTypewriter } from '../hooks/useTypewriter'
import { panelistName } from '../lib/labels'
import { currentStep, TURN_STEPS, type TurnStep } from '../lib/turnSteps'
import type { Notice, Turn, VoiceChoice } from '../state/flow'
import type { Player } from '../voice/player'
import { type QuestionVoice, useQuestionVoice } from '../voice/useQuestionVoice'
import { Bench, type Seat } from './Bench'
import { NoticeBanner } from './NoticeBanner'
import { SourcePassage } from './Sources'
import { Button, Card, DocumentIcon, Eyebrow, Heading, Working } from './ui'

// /start measured 30.6 s: three Gemini calls in a row. New copy about every 8 s.
const PREPARING_STAGES = [
  'The panel is preparing. They are reading your document.',
  'The panel is preparing. Each panelist is choosing what to press you on.',
  'The panel is preparing. They are agreeing who asks first.',
  'The panel is preparing. The first question is being written.',
]
const STAGE_MS = 8000

export const SEALED_TEXT = 'Answer recorded. Scores stay sealed until the debrief.'
export const GROUNDED_TEXT = 'Grounded in a passage from your document - revealed after you answer'

function seatsFromOptions(options: ArchetypeOption[]): Seat[] {
  return options.map((o) => ({ archetypeKey: o.archetype_key, surname: null, title: o.title, icon: o.icon }))
}

function seatsFromPanel(panel: PanelistOut[]): Seat[] {
  return panel.map((p) => ({ archetypeKey: p.archetype_key, surname: p.name, title: p.title, icon: p.icon }))
}

/** Panelists are known by archetype before /start returns; names come with the response. */
export function PreparingScreen({ seats }: { seats: ArchetypeOption[] }) {
  const [stage, setStage] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setStage((s) => Math.min(PREPARING_STAGES.length - 1, s + 1)), STAGE_MS)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className="space-y-6">
      {seats.length > 0 && <Bench seats={seatsFromOptions(seats)} active={null} status="preparing" />}
      <Card className="space-y-3">
        <Heading>
          <Working /> {PREPARING_STAGES[stage]}
        </Heading>
        <p className="text-sm text-muted">This usually takes 30–60 seconds.</p>
      </Card>
    </div>
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
    <p className="max-w-[62ch] cursor-text font-serif text-[19px] leading-[1.5] text-ink sm:text-[28px] sm:leading-[1.45] sm:tracking-[-0.005em]" onClick={skip}>
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
          {!submitting && (
            <Button variant="secondary" onClick={voice.replay} className="min-h-10">
              &#9654; Replay
            </Button>
          )}
          {voice.status === 'speaking' && <span className="font-mono text-xs text-muted">Speaking</span>}
        </div>
      )
  }
}

/** The previous question's reveal, once its answer is recorded: the passage the panelist
 * was reading, with the words the question was grounded in highlighted. No scores (K7). */
export function SourceReveal({ turn }: { turn: Turn }) {
  const { question, answer } = turn
  const asker = panelistName(question.panelist.name)
  return (
    <aside
      aria-label="Source passage"
      className="flex min-w-0 flex-[1_1_420px] animate-reveal flex-col gap-4 rounded-2xl border border-accent bg-card p-5 motion-reduce:animate-none sm:p-6"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-serif text-xl font-semibold">What {asker} was reading</h3>
        <span className="font-mono text-xs text-muted">Question {question.turn}</span>
      </div>
      <p className="flex items-center gap-2 text-sm text-muted">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M20 6 9 17l-5-5" />
        </svg>
        {SEALED_TEXT}
      </p>
      {question.sources.map((source, i) => (
        <SourcePassage key={i} source={source} />
      ))}
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

type QuestionScreenProps = {
  sessionId: string
  voiceChoice: VoiceChoice | null
  player: Player
  panel: PanelistOut[]
  question: QuestionOut
  draft: string
  last: Turn | null
  submitting: boolean
  notice: Notice | null
  onChange: (text: string) => void
  onSend: () => void
  onAutoRetry: () => void
}

export function QuestionScreen({ sessionId, voiceChoice, player, panel, question, draft, last, submitting, notice, onChange, onSend, onAutoRetry }: QuestionScreenProps) {
  const answerId = useId()
  const voice = useQuestionVoice({ player, choice: voiceChoice, sessionId, question, submitting })
  const [typed, setTyped] = useState(false)
  const counting = notice?.kind === 'busy' && notice.retryAt !== null
  const asker = panelistName(question.panelist.name)
  const asking = !typed || voice.status === 'loading' || voice.status === 'speaking'
  const step = currentStep({ submitting, asking })

  return (
    <div className="space-y-6">
      <Bench
        seats={seatsFromPanel(panel)}
        active={question.panelist.archetype_key}
        status={submitting ? 'reviewing' : 'asking'}
        speaking={voice.status === 'speaking'}
      />
      <div className="flex flex-wrap items-start gap-6">
        <section
          aria-label={`Question ${question.turn}`}
          className="flex min-w-0 flex-[1_1_520px] flex-col gap-6 rounded-2xl border border-line-strong bg-card p-5 sm:gap-7 sm:px-10 sm:pt-9 sm:pb-8"
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
                className="flex flex-col gap-2.5 border-t border-line pt-3.5"
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
                  rows={5}
                  value={draft}
                  maxLength={limits.AnswerRequest.text.maxLength}
                  onChange={(event) => onChange(event.target.value)}
                  className="w-full resize-y rounded-xl border border-line-strong bg-field p-4 text-base leading-relaxed text-ink placeholder:text-subtle focus:border-accent focus:outline-none"
                />
                {notice && (
                  <NoticeBanner notice={notice} onRetry={onSend} onAutoRetry={onAutoRetry} retryLabel={notice.kind === 'turnFailed' ? 'Send it again' : 'Try again'} />
                )}
                {!notice && (
                  // On a phone the submit bar stays at the bottom of the screen (bench-mobile).
                  <div className="sticky bottom-0 -mx-5 border-t border-line bg-ground px-4 pt-3 pb-6 sm:static sm:mx-0 sm:flex sm:justify-end sm:border-0 sm:bg-transparent sm:p-0">
                    <Button type="submit" disabled={draft.trim() === '' || counting} className="min-h-12 w-full sm:w-auto sm:px-7">
                      Submit answer
                    </Button>
                  </div>
                )}
              </form>
            </>
          )}
        </section>
        {last && <SourceReveal key={last.question.turn} turn={last} />}
      </div>
    </div>
  )
}

export function FinishingScreen({ notice, onRetry }: { notice: Notice | null; onRetry: () => void }) {
  return (
    <Card className="space-y-3">
      <Heading>
        <Working /> The panel is writing its report
      </Heading>
      <p className="text-sm text-muted">Your last answer is recorded. Putting the debrief together.</p>
      {notice && <NoticeBanner notice={notice} onRetry={onRetry} />}
    </Card>
  )
}
