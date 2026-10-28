// One screen per phase: the `switch` narrows `phase` to one variant per case, so each
// screen gets exactly the fields that variant carries.
import { ConfigScreen } from './components/ConfigScreen'
import { FinishingScreen, PreparingScreen, QuestionScreen } from './components/DefenseScreens'
import { ReportScreen } from './components/ReportScreen'
import { EmbeddingScreen, ExpiredScreen, FailedScreen, QueuedScreen, ReadyScreen, UploadingScreen } from './components/WaitScreens'
import { useState } from 'react'
import { DEFENSE_TYPE_LABELS } from './lib/labels'
import { seatedPanel } from './lib/panel'
import type { VoiceChoice } from './state/flow'
import { useFlow } from './state/useFlow'
import { createPlayer } from './voice/player'

export default function App() {
  const flow = useFlow()
  const [player] = useState(createPlayer)
  const { state } = flow
  const { phase } = state

  // Both clicks unlock audio, so the first question's voice can play when it arrives (E10).
  const chooseVoice = (voice: VoiceChoice) => {
    if (voice !== 'none') player.unlock()
    flow.chooseVoice(voice)
  }
  const start = () => {
    if (state.voice !== 'none') player.unlock()
    void flow.start()
  }

  let screen
  switch (phase.kind) {
    case 'configuring':
      screen = (
        <ConfigScreen
          draft={state.draft}
          options={state.options}
          quota={state.quota}
          notice={phase.notice}
          onChange={flow.editDraft}
          onSubmit={() => void flow.upload()}
        />
      )
      break
    case 'uploading':
      screen = (
        <UploadingScreen
          serverAwake={state.serverAwake}
          fileName={state.draft.file?.name ?? ''}
          loaded={phase.loaded}
          total={phase.total}
        />
      )
      break
    case 'queued':
      screen = <QueuedScreen position={phase.position} notice={phase.notice} onRetry={flow.resumePolling} />
      break
    case 'embedding':
      screen = <EmbeddingScreen progress={phase.progress} notice={phase.notice} onRetry={flow.resumePolling} />
      break
    case 'failed':
      screen = <FailedScreen message={phase.message} scanned={phase.scanned} onBack={flow.restart} />
      break
    case 'expired':
      screen = <ExpiredScreen onBack={flow.restart} />
      break
    case 'ready':
      screen = (
        <ReadyScreen
          chunkCount={phase.chunkCount}
          notice={phase.notice}
          voice={state.voice}
          ttsAvailable={state.ttsAvailable}
          onChooseVoice={chooseVoice}
          onStart={start}
          onAutoRetry={() => void flow.start(true)}
          onStartOver={flow.restart}
        />
      )
      break
    case 'preparing':
      screen = <PreparingScreen seats={state.options ? seatedPanel(state.options, state.draft.panel) : []} />
      break
    case 'question':
    case 'submitting':
      screen = (
        <QuestionScreen
          sessionId={phase.sessionId}
          voiceChoice={state.voice}
          player={player}
          panel={phase.panel}
          question={phase.question}
          draft={phase.draft}
          last={phase.last}
          submitting={phase.kind === 'submitting'}
          notice={phase.kind === 'question' ? phase.notice : null}
          onChange={flow.editAnswer}
          onSend={() => void flow.sendAnswer()}
          onAutoRetry={() => void flow.sendAnswer(true)}
        />
      )
      break
    case 'finishing':
      screen = <FinishingScreen notice={phase.notice} onRetry={() => void flow.retryReport()} />
      break
    case 'report':
      screen = <ReportScreen result={phase.report} options={state.options} onRestart={flow.restart} />
      break
  }

  // The bench and the debrief use the Bench design's wide layout; the rest stay a narrow column.
  const inDefense = phase.kind === 'question' || phase.kind === 'submitting'
  const width = inDefense || phase.kind === 'preparing' ? 'max-w-[1240px]' : phase.kind === 'report' ? 'max-w-4xl' : 'max-w-2xl'
  const subtitle = inDefense
    ? `${phase.topic} · ${DEFENSE_TYPE_LABELS[state.draft.panel.defense_type]} defense`
    : 'Face a panel that has read your paper.'

  return (
    <div className="min-h-dvh">
      <header className="border-b border-line-strong">
        <div className={`mx-auto flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-4 sm:px-6 sm:py-5 ${width}`}>
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-3.5 gap-y-1">
            <h1 className="font-serif text-lg font-semibold tracking-[-0.01em] sm:text-[22px]">Academic Defense Simulator</h1>
            <p className={`min-w-0 truncate ${inDefense ? 'font-mono text-xs text-muted' : 'text-sm text-muted'}`}>{subtitle}</p>
          </div>
          {inDefense && <p className="font-mono text-xs text-muted">Question {phase.question.turn}</p>}
        </div>
      </header>
      <main className={`mx-auto px-4 py-6 sm:px-6 ${width}`}>{screen}</main>
    </div>
  )
}
