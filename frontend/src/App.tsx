// One screen per phase: the `switch` narrows `phase` to one variant per case, so each
// screen gets exactly the fields that variant carries.
import { ConfigScreen } from './components/ConfigScreen'
import { FinishingScreen, PreparingScreen, QuestionScreen } from './components/DefenseScreens'
import { ReportScreen } from './components/ReportScreen'
import { EmbeddingScreen, ExpiredScreen, FailedScreen, QueuedScreen, ReadyScreen, UploadingScreen } from './components/WaitScreens'
import { useState } from 'react'
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
          topic={phase.topic}
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

  return (
    <div className="min-h-dvh">
      <header className="border-b border-line bg-card">
        <div className="mx-auto max-w-2xl px-4 py-4">
          <h1 className="font-serif text-xl">Academic Defense Simulator</h1>
          <p className="text-sm text-muted">Face a panel that has read your paper.</p>
        </div>
      </header>
      <main className="mx-auto max-w-2xl px-4 py-6">{screen}</main>
    </div>
  )
}
