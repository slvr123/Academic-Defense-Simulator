// Each wait between "upload" and "first question" is its own named state, never a bare
// spinner (D16). Copy uses measured times: cold start up to 42.7 s, 224 pages in 63.6 s.
import type { Notice } from '../state/flow'
import { NoticeBanner } from './NoticeBanner'
import { Button, Card, Heading, ProgressBar, Working } from './ui'

export function UploadingScreen({ serverAwake, fileName, loaded, total }: { serverAwake: boolean; fileName: string; loaded: number; total: number }) {
  if (!serverAwake) {
    return (
      <Card className="space-y-2">
        <Heading>
          <Working /> Waking the server - up to a minute on first visit
        </Heading>
        <p className="text-sm text-muted">The demo server sleeps when nobody is using it. Your upload starts as soon as it answers.</p>
      </Card>
    )
  }
  return (
    <Card className="space-y-3">
      <Heading>Uploading your document</Heading>
      <p className="truncate text-sm text-muted">{fileName}</p>
      <ProgressBar value={total > 0 ? loaded / total : 0} label="Upload progress" />
    </Card>
  )
}

export function QueuedScreen({ position, notice, onRetry }: { position: number; notice: Notice | null; onRetry: () => void }) {
  return (
    <Card className="space-y-3">
      <Heading>
        <Working /> In line - position {position}
      </Heading>
      <p className="text-sm text-muted">The server reads one document at a time. Yours is next once the ones ahead finish.</p>
      {notice && <NoticeBanner notice={notice} onRetry={onRetry} />}
    </Card>
  )
}

export function EmbeddingScreen({ progress, notice, onRetry }: { progress: number; notice: Notice | null; onRetry: () => void }) {
  return (
    <Card className="space-y-3">
      <Heading>Reading your document</Heading>
      <p className="text-sm text-muted">Large documents take about a minute.</p>
      <ProgressBar value={progress} label="Reading progress" />
      {notice && <NoticeBanner notice={notice} onRetry={onRetry} />}
    </Card>
  )
}

export function FailedScreen({ message, scanned, onBack }: { message: string; scanned: boolean; onBack: () => void }) {
  return (
    <Card className="space-y-3">
      <Heading>This document couldn't be read</Heading>
      <p>{scanned ? "This PDF needs selectable text - scanned pages can't be read." : message}</p>
      <Button onClick={onBack}>Choose another document</Button>
    </Card>
  )
}

export function ExpiredScreen({ onBack }: { onBack: () => void }) {
  return (
    <Card className="space-y-3">
      <Heading>This session expired - upload your document again.</Heading>
      <p className="text-sm text-muted">Sessions end after 30 idle minutes, or when the server restarts.</p>
      <Button onClick={onBack}>Upload again</Button>
    </Card>
  )
}

export function ReadyScreen({ chunkCount, notice, onStart, onAutoRetry, onStartOver }: {
  chunkCount: number | null
  notice: Notice | null
  onStart: () => void
  onAutoRetry: () => void
  onStartOver: () => void
}) {
  // A daily limit, or a document too short for this panel: starting again can't help.
  const stuck = notice?.kind === 'limited' || notice?.kind === 'message'
  return (
    <Card className="space-y-3">
      <Heading>Your document is ready</Heading>
      <p className="text-sm text-muted">
        {chunkCount === null ? 'The panel has read it.' : `The panel has read it: ${chunkCount} passages.`} Starting uses one of today's defenses.
      </p>
      {notice && (
        <NoticeBanner
          notice={notice}
          onRetry={onStart}
          onAutoRetry={onAutoRetry}
          turnFailedText="The panel couldn't ask its first question. Nothing extra was used - start again."
          retryLabel="Start again"
        />
      )}
      {stuck ? (
        <Button variant="secondary" onClick={onStartOver}>
          Start over
        </Button>
      ) : (
        !notice && <Button onClick={onStart}>Start the defense</Button>
      )}
    </Card>
  )
}
