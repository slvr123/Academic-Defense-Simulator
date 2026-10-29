// "Read a recorded defense" (M10 N5): one real defense on the bundled AniCheck sample,
// recorded once from the real API (scripts/record_demo.py) and replayed here through the
// real screens, a step at a time with "Next". It fetches only the static fixture: no
// backend call, no quota. Voice is the browser's or none, never panel voices.
import { type ReactNode, useEffect, useRef, useState } from 'react'
import type { AnswerResponse, PanelConfig, ReportResponse, SessionState, StartResponse } from '../api/types'
import { useStepIn } from '../motion/motion'
import type { Turn, VoiceChoice } from '../state/flow'
import { DEFENSE_TYPE_LABELS } from '../lib/labels'
import type { Player } from '../voice/player'
import { PreparingScreen, QuestionScreen } from './DefenseScreens'
import { DocumentPanel } from './DocumentPanel'
import { ReportScreen } from './ReportScreen'
import { SessionHeader, WORDMARK } from './SessionHeader'
import { Button, Card, Heading, Working } from './ui'

export const DEMO_PATH = '/demo/anicheck-defense.json'
/** Whether public/demo/anicheck-defense.json is shipped. False until it is recorded
 * (deferred to M10.1: Gemini answered 503 to every /start in M10); the start screen
 * hides "Read a recorded defense" until then. App.demo.test.tsx fails if this and the
 * file disagree, so committing the recording means flipping it. */
export const RECORDING_AVAILABLE = false
export const DEMO_PDF_PATH = '/sample/sample-capstone-anicheck.pdf'
export const DEMO_NOTE = 'Answers in this recording were written to demonstrate the panel.'

/** The fixture scripts/record_demo.py writes: every response verbatim, with the answers sent. */
export type DemoRecording = {
  recorded_at: string
  document: string
  panel_config: PanelConfig
  session: SessionState
  start: StartResponse
  turns: { answer: string; response: AnswerResponse }[]
  report: ReportResponse
}

type Load = { kind: 'loading' } | { kind: 'failed' } | { kind: 'ready'; recording: DemoRecording }

/** Step 0 is the preparing screen, steps 1..n the questions, n + 1 the debrief. */
function questionAt(recording: DemoRecording, step: number) {
  const question = step === 1 ? recording.start.question : recording.turns[step - 2]?.response.next_question
  return question ?? null
}

function answeredBefore(recording: DemoRecording, step: number): Turn[] {
  return recording.turns.slice(0, step - 1).map((turn, i) => ({
    question: questionAt(recording, i + 1) ?? recording.start.question,
    answer: turn.answer,
    evaluation: turn.response.evaluation,
  }))
}

export function DemoReplay({ player, onExit }: { player: Player; onExit: () => void }) {
  const [load, setLoad] = useState<Load>({ kind: 'loading' })
  const [step, setStep] = useState(0)
  const [voice, setVoice] = useState<VoiceChoice>('none')
  const [page, setPage] = useState<number | null>(null)
  const root = useRef<HTMLElement>(null)
  useStepIn(root, step)

  useEffect(() => {
    let cancelled = false
    fetch(DEMO_PATH)
      .then((response) => (response.ok ? (response.json() as Promise<DemoRecording>) : Promise.reject(new Error(String(response.status)))))
      .then((recording) => !cancelled && setLoad({ kind: 'ready', recording }))
      .catch(() => !cancelled && setLoad({ kind: 'failed' }))
    return () => {
      cancelled = true
    }
  }, [])

  const next = () => {
    setPage(null)
    setStep((s) => s + 1)
    window.scrollTo?.({ top: 0 })
  }
  const chooseVoice = (choice: VoiceChoice) => {
    if (choice !== 'none') player.unlock()
    setVoice(choice)
  }

  if (load.kind !== 'ready') {
    return (
      <Frame header={<Plain />}>
        <Card className="space-y-3">
          {load.kind === 'loading' ? (
            <Heading>
              <Working /> Loading the recorded defense
            </Heading>
          ) : (
            <>
              <Heading>The recorded defense couldn't be loaded</Heading>
              <Button onClick={onExit}>Back to the start</Button>
            </>
          )}
        </Card>
      </Frame>
    )
  }

  const { recording } = load
  const lastStep = recording.turns.length + 1
  const question = step >= 1 && step < lastStep ? questionAt(recording, step) : null
  const typeLabel = `${DEFENSE_TYPE_LABELS[recording.panel_config.defense_type]} defense`
  const badge = (
    <span className="rounded-md border border-accent px-2 py-0.5 font-mono text-[11px] tracking-[0.08em] text-accent uppercase">Recorded demo</span>
  )

  let header
  let screen
  if (question !== null) {
    const turns = answeredBefore(recording, step)
    header = (
      <SessionHeader
        subtitle={`${recording.start.topic} · ${typeLabel}`}
        turn={question.turn}
        badge={badge}
        voice={voice}
        ttsAvailable={false}
        panelDisclosed={false}
        onChooseVoice={chooseVoice}
        onOpenDocument={() => setPage(page === null ? (turns.at(-1)?.question.sources[0]?.page_start ?? 1) : null)}
        documentOpen={page !== null}
        endLabel="Leave the demo"
        onEnd={onExit}
      />
    )
    screen = (
      <QuestionScreen
        replay
        sessionId="recorded-demo"
        voiceChoice={voice}
        player={player}
        panel={recording.start.panel}
        question={question}
        draft={recording.turns[step - 1].answer}
        last={turns.at(-1) ?? null}
        turns={turns}
        submitting={false}
        notice={null}
        onChange={() => {}}
        onSend={next}
        onAutoRetry={() => {}}
        onOpenPage={setPage}
        documentPanel={page === null ? null : <DocumentPanel url={DEMO_PDF_PATH} page={page} onClose={() => setPage(null)} onBack={turns.length > 0 ? () => setPage(null) : null} sample />}
      />
    )
  } else if (step === 0) {
    header = <Plain subtitle={`${recording.start.topic} · ${typeLabel}`} badge={badge} />
    const seats = recording.start.panel.map((p) => ({ archetype_key: p.archetype_key, title: p.title, icon: p.icon }))
    screen = (
      <div className="space-y-5">
        <PreparingScreen seats={seats} ingest={recording.session.ingest ?? null} recorded />
        <div className="flex justify-end">
          <Button className="min-h-12 sm:px-7" onClick={next}>
            Next
          </Button>
        </div>
      </div>
    )
  } else {
    header = <Plain subtitle={`${recording.start.topic} · ${typeLabel}`} badge={badge} />
    screen = <ReportScreen result={recording.report} options={null} onRestart={onExit} restartLabel="Leave the demo" />
  }

  return (
    <Frame header={header} wide={step !== lastStep}>
      <p className="mb-5 rounded-xl border border-line-strong bg-surface px-4 py-3 text-sm text-muted print:hidden">
        <span className="font-semibold text-ink">Recorded demo.</span> {DEMO_NOTE} Nothing here calls the server or uses a defense.
      </p>
      <section ref={root}>{screen}</section>
    </Frame>
  )
}

function Plain({ subtitle, badge }: { subtitle?: string; badge?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="font-serif text-lg font-semibold tracking-[-0.01em] sm:text-xl">{WORDMARK}</h1>
        {badge}
      </span>
      {subtitle && <p className="min-w-0 truncate font-mono text-xs text-muted">{subtitle}</p>}
    </div>
  )
}

function Frame({ header, wide = true, children }: { header: ReactNode; wide?: boolean; children: ReactNode }) {
  const width = wide ? 'max-w-[1280px]' : 'max-w-4xl'
  return (
    <div className="min-h-dvh">
      <header className="border-b border-line-strong print:hidden">
        <div className={`mx-auto px-4 py-4 sm:px-6 ${width}`}>{header}</div>
      </header>
      <main className={`mx-auto px-4 py-6 sm:px-6 sm:py-7 ${width}`}>{children}</main>
    </div>
  )
}
