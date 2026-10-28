// Shows a Notice (state/flow.ts) the way D16 and its corrections say, with the way on.
import { useEffect, useRef, useState } from 'react'
import type { LimitCode } from '../api/types'
import { moreAt } from '../lib/manilaTime'
import type { Notice } from '../state/flow'
import { Button } from './ui'

/** The per-network limit comes from the 429 itself (M6 H6); an older server sends none. */
function perIpText(limit: number | null): string {
  if (limit === null) return "You've used this network's defenses for today."
  return `You've used your ${limit} defense${limit === 1 ? '' : 's'} for today.`
}

function limitedText(code: LimitCode | null, limit: number | null, resetsAt: number | null): string {
  const reason =
    code === 'per_ip_limit'
      ? perIpText(limit)
      : code === 'daily_cap'
        ? "Today's defenses are all used."
        : 'No more defenses can start right now.' // no code, or one this build doesn't know
  return resetsAt === null ? reason : `${reason} ${moreAt(new Date(resetsAt))}`
}

/** Counts down to the one automatic retry after a busy 503, then fires it once. */
function BusyCountdown({ retryAt, onElapsed }: { retryAt: number; onElapsed: () => void }) {
  const [now, setNow] = useState(() => Date.now())
  const fired = useRef(false)
  const left = Math.max(0, Math.ceil((retryAt - now) / 1000))

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    if (left > 0 || fired.current) return
    fired.current = true
    onElapsed()
  }, [left, onElapsed])

  return <p>The panel is busy - retrying in {left} s.</p>
}

type Props = {
  notice: Notice
  /** "Try again": repeats what failed. Omitted where trying again can't help. */
  onRetry?: () => void
  /** The one automatic retry after a busy 503 that said when (D16). */
  onAutoRetry?: () => void
  /** What a 502 means here: an answer, or the first question. */
  turnFailedText?: string
  retryLabel?: string
}

export function NoticeBanner({
  notice,
  onRetry,
  onAutoRetry,
  turnFailedText = "That turn didn't go through - nothing was recorded. Send it again.",
  retryLabel = 'Try again',
}: Props) {
  const tone =
    notice.kind === 'limited' || notice.kind === 'message'
      ? 'border-danger/40 bg-danger-soft text-ink'
      : 'border-warn/40 bg-warn-soft text-ink'
  const retry = onRetry && (
    <Button variant="secondary" onClick={onRetry} className="mt-3">
      {retryLabel}
    </Button>
  )

  let body
  switch (notice.kind) {
    case 'network':
      body = (
        <>
          <p>Can't reach the server.</p>
          {retry}
        </>
      )
      break
    case 'turnFailed':
      body = (
        <>
          <p>{turnFailedText}</p>
          {retry}
        </>
      )
      break
    case 'stillWorking':
      body = (
        <>
          <p>The panel is still working on your answer.</p>
          {retry}
        </>
      )
      break
    case 'busy':
      body =
        notice.retryAt !== null && onAutoRetry ? (
          <BusyCountdown retryAt={notice.retryAt} onElapsed={onAutoRetry} />
        ) : (
          <>
            <p>{notice.message}</p>
            {retry}
          </>
        )
      break
    case 'limited':
      body = <p>{limitedText(notice.code, notice.limit, notice.resetsAt)}</p>
      break
    case 'message':
      body = <p className="whitespace-pre-line">{notice.text}</p>
      break
  }

  return (
    <div role="alert" className={`rounded-lg border p-3 text-sm ${tone}`}>
      {body}
    </div>
  )
}
