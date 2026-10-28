// The React side of the flow: owns the reducer, makes the requests, and dispatches
// their results. Requests a person triggers (upload, start, answer) run from event
// handlers, not effects, so React's dev-mode double effect can't send them twice.
// Effects only do what is safe to repeat: waking, polling and keep-alive GETs.
import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import * as api from '../api/client'
import { useKeepAlive } from '../hooks/useKeepAlive'
import { createServerWaker } from '../lib/serverWaker'
import { clearAnswerDraft } from '../lib/answerDrafts'
import { withDefenseType } from '../lib/panel'
import { resolvedKeys } from '../lib/presets'
import { isTooLarge } from '../lib/upload'
import { type Draft, initialState, reducer, type State, type VoiceChoice } from './flow'

// M2 doesn't specify a poll interval; 2 s is D16's fallback.
export const POLL_INTERVAL_MS = 2000
// The bundled sample capstone (M10 N5), served by the static site from public/sample/.
export const SAMPLE_PDF_PATH = '/sample/sample-capstone-anicheck.pdf'
export const SAMPLE_FAILED_TEXT = "The sample paper couldn't be loaded. Check your connection and try again."

// A resent answer or start that meets the first one still running gets 409: wait,
// resend (E13; M6 H4 for /start).
export const CONFLICT_RETRY_MS = 3000
export const CONFLICT_RETRIES = 10

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export type Flow = {
  state: State
  editDraft: (draft: Partial<Draft>) => void
  upload: () => Promise<void>
  /** "Try it with the sample paper": loads the bundled capstone and uploads it as a capstone defense (N5). */
  trySample: () => Promise<void>
  resumePolling: () => void
  chooseVoice: (voice: VoiceChoice) => void
  start: (auto?: boolean) => Promise<void>
  editAnswer: (text: string) => void
  sendAnswer: (auto?: boolean) => Promise<void>
  retryReport: () => Promise<void>
  restart: () => void
  /** End session (M10 N2): DELETE the session, then back to the start screen whatever it answered. */
  endSession: () => Promise<void>
}

export function useFlow(): Flow {
  const [state, dispatch] = useReducer(reducer, undefined, initialState)
  // The probe that finds the server up also records whether it can make voices (E6).
  const [waker] = useState(() =>
    createServerWaker(async () => {
      const result = await api.getStatus()
      if (result.ok) dispatch({ type: 'serverAwake', ttsAvailable: result.data.tts_available })
      return result.ok
    }),
  )
  // Guards a second click landing before the first one's re-render disables the button.
  const requesting = useRef(false)
  const { phase } = state

  const wake = useCallback(() => waker.wake(), [waker])

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

  const ping = useCallback(() => void api.getStatus(), [])
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

  const upload = (draft: Draft = state.draft) =>
    once(async () => {
      const { file, preset } = draft
      let { panel } = draft
      if (phase.kind !== 'configuring' || file === null) return
      dispatch({ type: 'uploadRequested' })
      if (isTooLarge(file)) return // the reducer showed the message
      if (!(await wake())) {
        dispatch({ type: 'uploadFinished', result: { ok: false, error: { kind: 'network' } }, panel, now: Date.now() })
        return
      }
      // A preset is checked against /panel-options before it's sent (N7); a start
      // before the rosters arrived reads them now. Without them: the recommended panel.
      let options = state.options
      if (options === null) {
        const loaded = await api.getPanelOptions()
        if (loaded.ok) {
          options = loaded.data
          dispatch({ type: 'optionsLoaded', options })
        }
      }
      panel = { ...panel, archetype_keys: options === null ? null : resolvedKeys(options, panel, preset) }
      const result = await api.createSession(file, panel, (loaded, total) =>
        dispatch({ type: 'uploadProgress', loaded, total }),
      )
      dispatch({ type: 'uploadFinished', result, panel, now: Date.now() })
    })

  const start = (auto = false) =>
    once(async () => {
      if (phase.kind !== 'ready') return
      const { sessionId } = phase
      dispatch({ type: 'startRequested', auto })
      let result = await api.startDefense(sessionId)
      // A plain 409: the first /start is still running, or its response was lost and a
      // resend met it. Once it's done, a resend gets its response replayed (M6 H4).
      for (let tries = 0; !result.ok && result.error.kind === 'conflict' && tries < CONFLICT_RETRIES; tries++) {
        await sleep(CONFLICT_RETRY_MS)
        result = await api.startDefense(sessionId)
      }
      dispatch({ type: 'startFinished', sessionId, result, now: Date.now() })
    })

  const fetchReport = async (sessionId: string) => {
    const result = await api.getReport(sessionId)
    dispatch({ type: 'reportFinished', sessionId, result, now: Date.now() })
  }

  const sendAnswer = (auto = false) =>
    once(async () => {
      if (phase.kind !== 'question' || phase.draft.trim() === '') return
      const { sessionId, draft, question } = phase
      dispatch({ type: 'answerRequested', auto })
      let result = await api.submitAnswer(sessionId, draft, question.turn)
      // A plain 409: a request for this session is still running, most likely this same
      // answer sent before a lost response. Resending with the same turn either meets
      // it again or, once it's done, gets its stored response back (E13).
      for (let tries = 0; !result.ok && result.error.kind === 'conflict' && tries < CONFLICT_RETRIES; tries++) {
        await sleep(CONFLICT_RETRY_MS)
        result = await api.submitAnswer(sessionId, draft, question.turn)
      }
      if (!result.ok && result.error.kind === 'stale') {
        // The defense moved on without this answer (another tab): find where it is (M6 H5).
        const current = await api.getCurrent(sessionId)
        dispatch({ type: 'resynced', sessionId, stale: result.error, current, now: Date.now() })
        if (current.ok && current.data.done) await fetchReport(sessionId)
        return
      }
      dispatch({ type: 'answerFinished', sessionId, result, now: Date.now() })
      if (result.ok) clearAnswerDraft(sessionId, question.turn) // N8: sent, so the saved draft goes
      if (result.ok && result.data.done) await fetchReport(sessionId)
    })

  const retryReport = () =>
    once(async () => {
      if (phase.kind !== 'finishing') return
      dispatch({ type: 'reportRequested' })
      await fetchReport(phase.sessionId)
    })

  const trySample = async () => {
    if (phase.kind !== 'configuring' || requesting.current) return
    let file: File
    try {
      const response = await fetch(SAMPLE_PDF_PATH)
      if (!response.ok) throw new Error(String(response.status))
      file = new File([await response.blob()], 'sample-capstone-anicheck.pdf', { type: 'application/pdf' })
    } catch {
      dispatch({ type: 'startNotice', notice: { kind: 'message', text: SAMPLE_FAILED_TEXT } })
      return
    }
    const draft: Draft = { ...state.draft, file, panel: withDefenseType(state.draft.panel, 'capstone'), preset: 'recommended' }
    dispatch({ type: 'draftChanged', draft })
    await upload(draft)
  }

  return {
    state,
    editDraft: (draft) => dispatch({ type: 'draftChanged', draft }),
    upload: () => upload(),
    trySample,
    resumePolling: () => dispatch({ type: 'pollResumed' }),
    chooseVoice: (voice) => dispatch({ type: 'voiceChosen', voice }),
    start,
    editAnswer: (text) => dispatch({ type: 'answerChanged', text }),
    sendAnswer,
    retryReport,
    restart: () => dispatch({ type: 'restart' }),
    endSession: async () => {
      const sessionId = 'sessionId' in phase ? phase.sessionId : null
      if (sessionId !== null) await api.deleteSession(sessionId) // a 404 or a network error still ends it here
      dispatch({ type: 'restart' })
    },
  }
}
