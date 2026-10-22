// The React side of the flow: owns the reducer, makes the requests, and dispatches
// their results. Requests a person triggers (upload, start, answer) run from event
// handlers, not effects, so React's dev-mode double effect can't send them twice.
// Effects only do what is safe to repeat: waking, polling and keep-alive GETs.
import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import * as api from '../api/client'
import { useKeepAlive } from '../hooks/useKeepAlive'
import { createServerWaker } from '../lib/serverWaker'
import { isTooLarge } from '../lib/upload'
import { type Draft, initialState, reducer, type State } from './flow'

// M2 doesn't specify a poll interval; 2 s is D16's fallback.
export const POLL_INTERVAL_MS = 2000

export type Flow = {
  state: State
  editDraft: (draft: Partial<Draft>) => void
  upload: () => Promise<void>
  resumePolling: () => void
  start: (auto?: boolean) => Promise<void>
  editAnswer: (text: string) => void
  sendAnswer: (auto?: boolean) => Promise<void>
  retryReport: () => Promise<void>
  restart: () => void
}

const isUp = async () => (await api.getHealth()).ok

export function useFlow(): Flow {
  const [state, dispatch] = useReducer(reducer, undefined, initialState)
  const [waker] = useState(() => createServerWaker(isUp))
  // Guards a second click landing before the first one's re-render disables the button.
  const requesting = useRef(false)
  const { phase } = state

  const wake = useCallback(async () => {
    const up = await waker.wake()
    if (up) dispatch({ type: 'serverAwake' })
    return up
  }, [waker])

  // Page load: wake the server, then read the quota and the panel rosters (D15, D21).
  useEffect(() => {
    let cancelled = false
    void wake().then(async (up) => {
      if (!up || cancelled) return
      const [quota, options] = await Promise.all([api.getQuota(), api.getPanelOptions()])
      if (cancelled) return
      dispatch(quota.ok ? { type: 'quotaLoaded', quota: quota.data } : { type: 'quotaHidden' })
      if (options.ok) dispatch({ type: 'optionsLoaded', options: options.data })
    })
    return () => {
      cancelled = true
    }
  }, [wake])

  // Poll while the document waits or embeds, unless a notice paused it.
  const pollingId = (phase.kind === 'queued' || phase.kind === 'embedding') && !phase.notice ? phase.sessionId : null
  useEffect(() => {
    if (pollingId === null) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const tick = async () => {
      const result = await api.getSession(pollingId)
      if (cancelled) return
      dispatch({ type: 'sessionPolled', sessionId: pollingId, result, now: Date.now() })
      timer = setTimeout(tick, POLL_INTERVAL_MS)
    }
    timer = setTimeout(tick, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [pollingId])

  const ping = useCallback(() => void api.getHealth(), [])
  useKeepAlive(phase.kind === 'question' || phase.kind === 'submitting', ping)

  async function once(run: () => Promise<void>): Promise<void> {
    if (requesting.current) return
    requesting.current = true
    try {
      await run()
    } finally {
      requesting.current = false
    }
  }

  const upload = () =>
    once(async () => {
      const { file, panel } = state.draft
      if (phase.kind !== 'configuring' || file === null) return
      dispatch({ type: 'uploadRequested' })
      if (isTooLarge(file)) return // the reducer showed the message
      if (!(await wake())) {
        dispatch({ type: 'uploadFinished', result: { ok: false, error: { kind: 'network' } }, now: Date.now() })
        return
      }
      const result = await api.createSession(file, panel, (loaded, total) =>
        dispatch({ type: 'uploadProgress', loaded, total }),
      )
      dispatch({ type: 'uploadFinished', result, now: Date.now() })
    })

  const start = (auto = false) =>
    once(async () => {
      if (phase.kind !== 'ready') return
      const { sessionId } = phase
      dispatch({ type: 'startRequested', auto })
      const result = await api.startDefense(sessionId)
      dispatch({ type: 'startFinished', sessionId, result, now: Date.now() })
    })

  const fetchReport = async (sessionId: string) => {
    const result = await api.getReport(sessionId)
    dispatch({ type: 'reportFinished', sessionId, result, now: Date.now() })
  }

  const sendAnswer = (auto = false) =>
    once(async () => {
      if (phase.kind !== 'question' || phase.draft.trim() === '') return
      const { sessionId, draft } = phase
      dispatch({ type: 'answerRequested', auto })
      const result = await api.submitAnswer(sessionId, draft)
      dispatch({ type: 'answerFinished', sessionId, result, now: Date.now() })
      if (result.ok && result.data.done) await fetchReport(sessionId)
    })

  const retryReport = () =>
    once(async () => {
      if (phase.kind !== 'finishing') return
      dispatch({ type: 'reportRequested' })
      await fetchReport(phase.sessionId)
    })

  return {
    state,
    editDraft: (draft) => dispatch({ type: 'draftChanged', draft }),
    upload,
    resumePolling: () => dispatch({ type: 'pollResumed' }),
    start,
    editAnswer: (text) => dispatch({ type: 'answerChanged', text }),
    sendAnswer,
    retryReport,
    restart: () => dispatch({ type: 'restart' }),
  }
}
