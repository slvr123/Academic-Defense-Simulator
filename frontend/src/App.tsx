// One screen per phase: the `switch` narrows `phase` to one variant per case, so each
// screen gets exactly the fields that variant carries.
import { ConfigScreen } from './components/ConfigScreen'
import { Card } from './components/ui'
import { EmbeddingScreen, ExpiredScreen, FailedScreen, QueuedScreen, ReadyScreen, UploadingScreen } from './components/WaitScreens'
import { useFlow } from './state/useFlow'

export default function App() {
  const flow = useFlow()
  const { state } = flow
  const { phase } = state

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
          onStart={() => void flow.start()}
          onAutoRetry={() => void flow.start(true)}
          onStartOver={flow.restart}
        />
      )
      break
    case 'preparing':
    case 'question':
    case 'submitting':
    case 'finishing':
    case 'report':
      screen = <Card>The defense screens arrive in the next slice.</Card>
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
