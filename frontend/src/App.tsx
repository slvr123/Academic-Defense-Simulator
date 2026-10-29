// One screen per phase: the `switch` narrows `phase` to one variant per case, so each
// screen gets exactly the fields that variant carries.
import { useState } from 'react'
import { FinishingScreen, PreparingScreen, QuestionScreen } from './components/DefenseScreens'
import { DemoReplay, RECORDING_AVAILABLE } from './components/DemoReplay'
import { DocumentPanel } from './components/DocumentPanel'
import { QuotaLine } from './components/QuotaLine'
import { ReportScreen } from './components/ReportScreen'
import { SessionHeader, WORDMARK } from './components/SessionHeader'
import { StartScreen } from './components/StartScreen'
import { EmbeddingScreen, ExpiredScreen, FailedScreen, QueuedScreen, ReadyScreen, UploadingScreen } from './components/WaitScreens'
import { useObjectUrl } from './hooks/useObjectUrl'
import { DEFENSE_TYPE_LABELS } from './lib/labels'
import { seatedPanel } from './lib/panel'
import type { VoiceChoice } from './state/flow'
import { useFlow } from './state/useFlow'
import { createPlayer } from './voice/player'

export const TAGLINE = 'Face a panel that has read your paper.'

export default function App() {
  const flow = useFlow()
  const [player] = useState(createPlayer)
  const { state } = flow
  const { phase } = state
  const [demo, setDemo] = useState(false) // "Read a recorded defense" (N5)
  const inDefense = phase.kind === 'question' || phase.kind === 'submitting'

  // N4: the candidate's own PDF, as an object URL while the defense runs. It goes (and is
  // revoked) when the defense ends, and the buttons hide when there's no file.
  const ownPdf = useObjectUrl(inDefense ? state.draft.file : null)
  const [opened, setOpened] = useState<{ sessionId: string; page: number } | null>(null)
  const documentPage = inDefense && opened?.sessionId === phase.sessionId ? opened.page : null
  const openPage =
    inDefense && ownPdf.available
      ? (page: number) => {
          ownPdf.ensure()
          setOpened({ sessionId: phase.sessionId, page })
        }
      : null
  // "Your document" opens the last revealed page, or page 1, and closes the panel when it's open.
  const lastPage = inDefense ? phase.last?.question.sources[0]?.page_start : undefined
  const toggleDocument = openPage && (() => (documentPage === null ? openPage(lastPage ?? 1) : setOpened(null)))

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
        <StartScreen
          draft={state.draft}
          options={state.options}
          quota={state.quota}
          notice={phase.notice}
          onTrySample={() => void flow.trySample()}
          onReadRecording={RECORDING_AVAILABLE ? () => setDemo(true) : null}
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
      screen = <PreparingScreen seats={state.options ? seatedPanel(state.options, state.draft.panel) : []} ingest={phase.ingest} />
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
          turns={phase.turns}
          submitting={phase.kind === 'submitting'}
          notice={phase.kind === 'question' ? phase.notice : null}
          onChange={flow.editAnswer}
          onSend={() => void flow.sendAnswer()}
          onAutoRetry={() => void flow.sendAnswer(true)}
          onOpenPage={openPage}
          documentPanel={ownPdf.url && documentPage !== null ? <DocumentPanel url={ownPdf.url} page={documentPage} onClose={() => setOpened(null)} onBack={phase.last ? () => setOpened(null) : null} /> : null}
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

  const defenseType = `${DEFENSE_TYPE_LABELS[state.draft.panel.defense_type]} defense`
  const wide = inDefense || phase.kind === 'preparing' || phase.kind === 'configuring'
  const width = wide ? 'max-w-[1280px]' : phase.kind === 'report' ? 'max-w-4xl' : 'max-w-2xl'

  let header
  if (inDefense) {
    header = (
      <SessionHeader
        subtitle={`${phase.topic} · ${defenseType}`}
        turn={phase.question.turn}
        voice={state.voice}
        ttsAvailable={state.ttsAvailable}
        panelDisclosed={state.panelDisclosed}
        onChooseVoice={chooseVoice}
        onOpenDocument={toggleDocument}
        documentOpen={documentPage !== null}
        onEnd={() => void flow.endSession()}
      />
    )
  } else {
    const subtitle = phase.kind === 'preparing' ? `${state.draft.panel.topic ?? state.draft.file?.name ?? 'Your paper'} · ${defenseType}` : TAGLINE
    header = (
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h1 className="font-serif text-lg font-semibold tracking-[-0.01em] sm:text-xl">{WORDMARK}</h1>
          {phase.kind !== 'configuring' && (
            <p className={`min-w-0 truncate ${phase.kind === 'preparing' ? 'font-mono text-xs' : 'text-sm'} text-muted`}>{subtitle}</p>
          )}
        </div>
        {phase.kind === 'configuring' && <QuotaLine quota={state.quota} />}
      </div>
    )
  }

  if (demo) return <DemoReplay player={player} onExit={() => setDemo(false)} />

  return (
    <div className="min-h-dvh">
      <header className="border-b border-line-strong print:hidden">
        <div className={`mx-auto px-4 py-4 sm:px-6 ${width}`}>{header}</div>
      </header>
      <main className={`mx-auto px-4 py-6 sm:px-6 sm:py-7 ${width}`}>{screen}</main>
    </div>
  )
}
