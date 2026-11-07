// One screen per phase: the `switch` narrows `phase` to one variant per case, so each
// screen gets exactly the fields that variant carries.
import { useState } from 'react'
import { FinishingScreen, PreparingScreen, QuestionScreen } from './components/DefenseScreens'
import { DemoReplay, RECORDING_AVAILABLE } from './components/DemoReplay'
import { DocumentPanel } from './components/DocumentPanel'
import { HowItWorks, HowItWorksLink } from './components/HowItWorks'
import { OwnKeyPanel } from './components/OwnKeyPanel'
import { QuotaLine } from './components/QuotaLine'
import { ReportScreen } from './components/ReportScreen'
import { HomeTitle, SessionHeader } from './components/SessionHeader'
import { ThemeToggle } from './components/ThemeToggle'
import { StartScreen } from './components/StartScreen'
import { EmbeddingScreen, ExpiredScreen, FailedScreen, QueuedScreen, ReadyScreen, UploadingScreen, WakingNotice } from './components/WaitScreens'
import { useObjectUrl } from './hooks/useObjectUrl'
import { sessionSubtitle } from './lib/labels'
import { heardFrom, seatedPanel } from './lib/panel'
import { quotaUsedUp, type VoiceChoice } from './state/flow'
import { useFlow } from './state/useFlow'
import { createPlayer } from './voice/player'

export const TAGLINE = 'Face a panel that has read your paper.'

export default function App() {
  const flow = useFlow()
  const [player] = useState(createPlayer)
  const { state } = flow
  const { phase } = state
  const [demo, setDemo] = useState(false) // "Read a recorded defense" (N5)
  // M17: the own-key panel, opened from the header; open on its own once today's free defenses are used.
  const [keyOpen, setKeyOpen] = useState(false)
  const freeUsedUp = quotaUsedUp(state.quota)
  const showKeyPanel = keyOpen || freeUsedUp || state.ownKey
  const inDefense = phase.kind === 'question' || phase.kind === 'submitting'
  const taglish = state.draft.panel.panel_language === 'taglish' // M16: the panel speaks Taglish

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
        <div className="space-y-5">
          {state.waking && <WakingNotice />}
          {showKeyPanel && (
            <OwnKeyPanel active={state.ownKey} prominent={freeUsedUp} onSubmit={flow.submitOwnKey} onRemove={flow.dropOwnKey} />
          )}
          <StartScreen
            draft={state.draft}
            options={state.options}
            quota={state.quota}
            notice={phase.notice}
            onTrySample={() => void flow.trySample()}
            onReadRecording={RECORDING_AVAILABLE ? () => setDemo(true) : null}
            taglishMode={state.taglishMode}
            ownKey={state.ownKey}
            onChange={flow.editDraft}
            onSubmit={() => void flow.upload()}
          />
          <HowItWorks />
        </div>
      )
      break
    case 'uploading':
      screen = (
        <UploadingScreen
          waking={!state.serverAwake || state.waking}
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
          taglish={taglish}
          onChooseVoice={chooseVoice}
          onStart={start}
          onAutoRetry={() => void flow.start(true)}
          onStartOver={() => void flow.endSession()} // DELETE: a held slot comes back (M11 P1)
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
          closing={phase.closing ?? null}
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

  const wide = inDefense || phase.kind === 'preparing' || phase.kind === 'configuring'
  const width = wide ? 'max-w-[1280px]' : phase.kind === 'report' ? 'max-w-4xl' : 'max-w-2xl'

  let header
  if (inDefense) {
    header = (
      <SessionHeader
        subtitle={sessionSubtitle(phase.topic, state.draft.panel)}
        turn={phase.question.turn}
        heard={heardFrom(phase.panel, [...phase.turns.map((t) => t.question.panelist.archetype_key), phase.question.panelist.archetype_key])}
        voice={state.voice}
        ttsAvailable={state.ttsAvailable}
        taglish={taglish}
        panelDisclosed={state.panelDisclosed}
        onChooseVoice={chooseVoice}
        onOpenDocument={toggleDocument}
        documentOpen={documentPage !== null}
        onEnd={() => void flow.endSession()}
      />
    )
  } else {
    // M13 D0c: the wordmark goes home. Where a session or an upload is under way, it asks
    // first and then does exactly what End session does (the DELETE, refund rules as they
    // are); where nothing is lost (the debrief, a failed or expired upload), it goes at once.
    const leaving = phase.kind === 'uploading' || phase.kind === 'queued' || phase.kind === 'embedding' || phase.kind === 'ready' || phase.kind === 'preparing' || phase.kind === 'finishing'
    const home = phase.kind === 'configuring' ? null : leaving ? () => void flow.endSession() : flow.restart
    const subtitle = phase.kind === 'preparing' ? sessionSubtitle(state.draft.panel.topic ?? state.draft.file?.name ?? 'Your paper', state.draft.panel) : TAGLINE
    header = (
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <div className="flex min-w-0 flex-col gap-0.5">
          <HomeTitle onHome={home} confirm={leaving} />
          {phase.kind !== 'configuring' && (
            <p className={`min-w-0 truncate ${phase.kind === 'preparing' ? 'font-mono text-xs' : 'text-sm'} text-muted`}>{subtitle}</p>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1">
          {phase.kind === 'configuring' && (
            <>
              <HowItWorksLink />
              <QuotaLine quota={state.quota} ownKey={state.ownKey} />
              {!freeUsedUp && !state.ownKey && (
                <button
                  type="button"
                  aria-expanded={keyOpen}
                  aria-controls="own-key"
                  onClick={() => setKeyOpen((open) => !open)}
                  className="inline-flex min-h-11 items-center text-sm text-action-text underline-offset-[3px] hover:underline"
                >
                  Use your own Gemini key
                </button>
              )}
            </>
          )}
          <ThemeToggle />
        </div>
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
