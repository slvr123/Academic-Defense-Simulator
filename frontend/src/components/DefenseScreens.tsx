// The answer loop: preparing, question, the last answer's evaluation and sources, finishing.
import { useEffect, useId, useState } from 'react'
import { limits } from '../api/constraints.gen'
import type { ArchetypeOption, PanelistOut, QuestionOut } from '../api/types'
import { useTypewriter } from '../hooks/useTypewriter'
import { accentFor } from '../lib/accents'
import { panelistName } from '../lib/labels'
import type { Notice, Turn, VoiceChoice } from '../state/flow'
import type { Player } from '../voice/player'
import { type QuestionVoice, useQuestionVoice } from '../voice/useQuestionVoice'
import { Avatar } from './Avatar'
import { NoticeBanner } from './NoticeBanner'
import { EvaluationScores, ScaleCaption } from './Scores'
import { Sources } from './Sources'
import { Button, Card, Heading, Working } from './ui'

// /start measured 30.6 s: three Gemini calls in a row. New copy about every 8 s.
const PREPARING_STAGES = [
  'The panel is preparing. They are reading your document.',
  'The panel is preparing. Each panelist is choosing what to press you on.',
  'The panel is preparing. They are agreeing who asks first.',
  'The panel is preparing. The first question is being written.',
]
const STAGE_MS = 8000

/** Panelists are known by archetype before /start returns; names come with the response. */
export function PreparingScreen({ seats }: { seats: ArchetypeOption[] }) {
  const [stage, setStage] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setStage((s) => Math.min(PREPARING_STAGES.length - 1, s + 1)), STAGE_MS)
    return () => clearInterval(timer)
  }, [])

  return (
    <Card className="space-y-4">
      <Heading>
        <Working /> {PREPARING_STAGES[stage]}
      </Heading>
      <p className="text-sm text-muted">This usually takes 30–60 seconds.</p>
      {seats.length > 0 && (
        <ul className="flex flex-wrap gap-3" aria-label="Seated panel">
          {seats.map((seat) => (
            <li key={seat.archetype_key} className="flex items-center gap-2 text-sm">
              <Avatar icon={seat.icon} archetypeKey={seat.archetype_key} size="sm" />
              {seat.title}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function PanelStrip({ panel, asking, speaking }: { panel: PanelistOut[]; asking: string; speaking: boolean }) {
  return (
    <ul className="flex gap-3 overflow-x-auto pb-1" aria-label="Panel">
      {panel.map((p) => {
        const active = p.archetype_key === asking
        return (
          <li key={p.archetype_key} className={`flex min-w-0 shrink-0 flex-col items-center gap-1 text-center transition-opacity ${active ? '' : 'opacity-50'}`}>
            <Avatar icon={p.icon} archetypeKey={p.archetype_key} size={active ? 'lg' : 'md'} speaking={active && speaking} />
            <span className="max-w-24 truncate text-xs font-medium">{panelistName(p.name)}</span>
          </li>
        )
      })}
    </ul>
  )
}

/** Mounted once per question (keyed by turn), so the reveal restarts for each. */
function QuestionText({ text }: { text: string }) {
  const { shown, done, skip } = useTypewriter(text)
  return (
    <p className="cursor-text font-serif text-lg leading-relaxed" onClick={skip}>
      <span className="sr-only">{text}</span>
      <span aria-hidden>
        {shown}
        {!done && <span className="ml-0.5 inline-block w-2 animate-pulse bg-ink/60 motion-reduce:animate-none">&nbsp;</span>}
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
        <p className="flex items-center gap-2 text-xs text-muted">
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
            <Button variant="secondary" onClick={voice.replay} className="py-1.5">
              &#9654; Replay
            </Button>
          )}
          {voice.status === 'speaking' && <span className="text-xs text-muted">Speaking</span>}
        </div>
      )
  }
}

function QuestionCard({ question, voice, submitting }: { question: QuestionOut; voice: QuestionVoice; submitting: boolean }) {
  const { panelist } = question
  const accent = accentFor(panelist.archetype_key)
  return (
    <Card className={`space-y-3 border-l-4 ${accent.border}`}>
      <div className="flex items-center gap-3">
        <Avatar icon={panelist.icon} archetypeKey={panelist.archetype_key} speaking={voice.status === 'speaking'} />
        <div className="min-w-0">
          <p className="font-medium">{panelistName(panelist.name)}</p>
          <p className={`text-sm ${accent.text}`}>{panelist.title}</p>
        </div>
        <span className="ml-auto shrink-0 text-xs text-muted">Question {question.turn}</span>
      </div>
      <QuestionText key={question.turn} text={question.question} />
      <VoiceControls voice={voice} submitting={submitting} />
    </Card>
  )
}

export function LastTurn({ turn }: { turn: Turn }) {
  const { evaluation, question } = turn
  return (
    <Card className="space-y-3">
      <p className="text-sm font-medium">
        How the panel scored your answer to question {question.turn}
      </p>
      <EvaluationScores evaluation={evaluation} />
      <ScaleCaption />
      <p className="text-sm">{evaluation.answer_summary}</p>
      {evaluation.primary_gap && (
        <p className="text-sm">
          <span className="font-medium">What was missing: </span>
          {evaluation.primary_gap}
        </p>
      )}
      <details className="text-sm" open>
        <summary className="cursor-pointer font-medium">The passage that question came from</summary>
        <div className="mt-2">
          <Sources sources={question.sources} />
        </div>
      </details>
    </Card>
  )
}

type QuestionScreenProps = {
  sessionId: string
  voiceChoice: VoiceChoice | null
  player: Player
  topic: string
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

export function QuestionScreen({ sessionId, voiceChoice, player, topic, panel, question, draft, last, submitting, notice, onChange, onSend, onAutoRetry }: QuestionScreenProps) {
  const answerId = useId()
  const voice = useQuestionVoice({ player, choice: voiceChoice, sessionId, question, submitting })
  const counting = notice?.kind === 'busy' && notice.retryAt !== null
  return (
    <div className="space-y-4">
      <div className="space-y-3">
        <p className="truncate text-sm text-muted">{topic}</p>
        <PanelStrip panel={panel} asking={question.panelist.archetype_key} speaking={voice.status === 'speaking'} />
      </div>
      {last && <LastTurn turn={last} />}
      <QuestionCard question={question} voice={voice} submitting={submitting} />
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          onSend()
        }}
      >
        <label htmlFor={answerId} className="block text-sm font-medium">
          Your answer
        </label>
        <textarea
          id={answerId}
          rows={6}
          value={draft}
          readOnly={submitting}
          maxLength={limits.AnswerRequest.text.maxLength}
          onChange={(event) => onChange(event.target.value)}
          className="w-full rounded-lg border border-line bg-card p-3 text-base leading-relaxed focus:border-brand focus:outline-none read-only:bg-paper"
        />
        {notice && (
          <NoticeBanner notice={notice} onRetry={onSend} onAutoRetry={onAutoRetry} retryLabel={notice.kind === 'turnFailed' ? 'Send it again' : 'Try again'} />
        )}
        {submitting ? (
          <p className="flex items-center gap-2 text-sm text-muted">
            <Working /> The panel is weighing your answer.
          </p>
        ) : (
          !notice && (
            <Button type="submit" disabled={draft.trim() === '' || counting}>
              Send answer
            </Button>
          )
        )}
      </form>
    </div>
  )
}

export function FinishingScreen({ notice, onRetry }: { notice: Notice | null; onRetry: () => void }) {
  return (
    <Card className="space-y-3">
      <Heading>
        <Working /> The panel is writing its report
      </Heading>
      <p className="text-sm text-muted">Your last answer is scored. Putting the report together.</p>
      {notice && <NoticeBanner notice={notice} onRetry={onRetry} />}
    </Card>
  )
}
