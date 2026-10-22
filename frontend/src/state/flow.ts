// The defense flow as one state machine (D6). A pure function with no React and no
// network: requests happen in useFlow.ts, and their results come back here as actions.
// Every action that needs the time carries `now`, so the reducer stays pure.
import type { ApiError, ApiResult } from '../api/client'
import { defaults } from '../api/constraints.gen'
import type {
  AnswerResponse,
  Evaluation,
  LimitCode,
  PanelConfig,
  PanelistOut,
  PanelOptions,
  QuestionOut,
  QuotaStatus,
  ReportResponse,
  SessionCreated,
  SessionState,
  StartResponse,
} from '../api/types'
import { isTooLarge, TOO_LARGE_MESSAGE } from '../lib/upload'

/** What went wrong, in the terms the screen needs to show it and offer a way on. */
export type Notice =
  | { kind: 'network' }
  | { kind: 'turnFailed' }
  // retryAt: when the one automatic retry fires (epoch ms); null means a "Try again" button only.
  | { kind: 'busy'; message: string; retryAt: number | null }
  | { kind: 'limited'; code: LimitCode | null; resetsAt: number | null }
  | { kind: 'message'; text: string }

export type Draft = { file: File | null; panel: PanelConfig }

export type QuotaView = { kind: 'loading' } | { kind: 'hidden' } | { kind: 'known'; quota: QuotaStatus }

/** An answered question, shown with its evaluation and, now that it's answered, its sources (D18). */
export type Turn = { question: QuestionOut; answer: string; evaluation: Evaluation }

type Defense = {
  sessionId: string
  topic: string
  panel: PanelistOut[]
  question: QuestionOut
  draft: string // the answer box; kept through a failed send (D16, 502)
  last: Turn | null
}

export type Phase =
  | { kind: 'configuring'; notice: Notice | null }
  | { kind: 'uploading'; loaded: number; total: number }
  | { kind: 'queued'; sessionId: string; position: number; notice: Notice | null }
  | { kind: 'embedding'; sessionId: string; progress: number; notice: Notice | null }
  | { kind: 'failed'; message: string; scanned: boolean }
  | { kind: 'expired' } // a 404: the session is gone (D16)
  | { kind: 'ready'; sessionId: string; chunkCount: number | null; notice: Notice | null }
  // auto: this request is the one automatic retry after a busy 503, so no second one follows.
  | { kind: 'preparing'; sessionId: string; auto: boolean }
  | ({ kind: 'question'; notice: Notice | null } & Defense)
  | ({ kind: 'submitting'; auto: boolean } & Defense)
  | { kind: 'finishing'; sessionId: string; notice: Notice | null }
  | { kind: 'report'; report: ReportResponse }

export type State = {
  serverAwake: boolean // a flag beside the phase, not a phase (D25)
  options: PanelOptions | null
  quota: QuotaView
  draft: Draft
  phase: Phase
}

export type Action =
  | { type: 'serverAwake' }
  | { type: 'optionsLoaded'; options: PanelOptions }
  | { type: 'quotaLoaded'; quota: QuotaStatus }
  | { type: 'quotaHidden' }
  | { type: 'draftChanged'; draft: Partial<Draft> }
  | { type: 'uploadRequested' }
  | { type: 'uploadProgress'; loaded: number; total: number }
  | { type: 'uploadFinished'; result: ApiResult<SessionCreated>; now: number }
  | { type: 'sessionPolled'; sessionId: string; result: ApiResult<SessionState>; now: number }
  | { type: 'pollResumed' }
  | { type: 'startRequested'; auto: boolean }
  | { type: 'startFinished'; sessionId: string; result: ApiResult<StartResponse>; now: number }
  | { type: 'answerChanged'; text: string }
  | { type: 'answerRequested'; auto: boolean }
  | { type: 'answerFinished'; sessionId: string; result: ApiResult<AnswerResponse>; now: number }
  | { type: 'reportRequested' }
  | { type: 'reportFinished'; sessionId: string; result: ApiResult<ReportResponse>; now: number }
  | { type: 'restart' }

/** No defenses left today, so Start is disabled (D15). */
export function quotaUsedUp(quota: QuotaView): boolean {
  return quota.kind === 'known' && quota.quota.remaining === 0
}

export function initialState(): State {
  return {
    serverAwake: false,
    options: null,
    quota: { kind: 'loading' },
    draft: { file: null, panel: { ...defaults.PanelConfig } },
    phase: { kind: 'configuring', notice: null },
  }
}

/** The notice for a failed request. A busy 503 earns one automatic retry, and only
 * when the server said when (Retry-After) and this wasn't already that retry (D23). */
export function noticeFor(error: ApiError, now: number, autoRetryAllowed: boolean): Notice {
  switch (error.kind) {
    case 'network':
      return { kind: 'network' }
    case 'turnFailed':
      return { kind: 'turnFailed' }
    case 'busy':
      return {
        kind: 'busy',
        message: error.message,
        retryAt: autoRetryAllowed && error.retryAfterS !== null ? now + error.retryAfterS * 1000 : null,
      }
    case 'limited':
      return {
        kind: 'limited',
        code: error.code,
        resetsAt: error.retryAfterS !== null ? now + error.retryAfterS * 1000 : null,
      }
    case 'notFound': // callers send a 404 to the expired phase before asking for a notice
    case 'conflict':
    case 'tooLarge':
    case 'invalid':
    case 'unexpected':
      return { kind: 'message', text: error.message }
  }
}

/** The session is gone (D16, 404): clear the file, since it has to be uploaded again. */
function expired(state: State): State {
  return { ...state, draft: { ...state.draft, file: null }, phase: { kind: 'expired' } }
}

function inFlight<K extends Phase['kind']>(phase: Phase, kind: K, sessionId: string): phase is Extract<Phase, { kind: K; sessionId: string }> {
  return phase.kind === kind && 'sessionId' in phase && phase.sessionId === sessionId
}

function polled(state: State, sessionId: string, result: ApiResult<SessionState>, now: number): State {
  const { phase } = state
  if (!(inFlight(phase, 'queued', sessionId) || inFlight(phase, 'embedding', sessionId)) || phase.notice) return state
  if (!result.ok) {
    if (result.error.kind === 'notFound') return expired(state)
    return { ...state, phase: { ...phase, notice: noticeFor(result.error, now, false) } }
  }
  const session = result.data
  switch (session.status) {
    case 'queued':
      return { ...state, phase: { kind: 'queued', sessionId, position: session.queue_position ?? 1, notice: null } }
    case 'embedding':
      return { ...state, phase: { kind: 'embedding', sessionId, progress: session.progress ?? 0, notice: null } }
    case 'ready':
      return { ...state, phase: { kind: 'ready', sessionId, chunkCount: session.chunk_count ?? null, notice: null } }
    case 'failed':
      return {
        ...state,
        phase: {
          kind: 'failed',
          message: session.error ?? "This document couldn't be read.",
          scanned: session.error_code === 'scanned_pdf',
        },
      }
  }
}

function started(state: State, sessionId: string, result: ApiResult<StartResponse>, now: number): State {
  const { phase } = state
  if (!inFlight(phase, 'preparing', sessionId)) return state
  if (!result.ok) {
    if (result.error.kind === 'conflict') return state // D16: swallowed, the in-flight state continues
    if (result.error.kind === 'notFound') return expired(state)
    return { ...state, phase: { kind: 'ready', sessionId, chunkCount: null, notice: noticeFor(result.error, now, !phase.auto) } }
  }
  const { panel, question, topic } = result.data
  return { ...state, phase: { kind: 'question', sessionId, topic, panel, question, draft: '', last: null, notice: null } }
}

function answered(state: State, sessionId: string, result: ApiResult<AnswerResponse>, now: number): State {
  const { phase } = state
  if (!inFlight(phase, 'submitting', sessionId)) return state
  const { auto, ...defense } = phase
  if (!result.ok) {
    if (result.error.kind === 'conflict') return state
    if (result.error.kind === 'notFound') return expired(state)
    return { ...state, phase: { ...defense, kind: 'question', notice: noticeFor(result.error, now, !auto) } }
  }
  const { evaluation, next_question, done } = result.data
  if (done) return { ...state, phase: { kind: 'finishing', sessionId, notice: null } }
  if (next_question === null) {
    const text = 'The panel answered without a next question. Try sending your answer again.'
    return { ...state, phase: { ...defense, kind: 'question', notice: { kind: 'message', text } } }
  }
  const last: Turn = { question: defense.question, answer: defense.draft, evaluation }
  return { ...state, phase: { ...defense, kind: 'question', question: next_question, draft: '', last, notice: null } }
}

function reported(state: State, sessionId: string, result: ApiResult<ReportResponse>, now: number): State {
  const { phase } = state
  if (!inFlight(phase, 'finishing', sessionId) || phase.notice) return state
  if (!result.ok) {
    if (result.error.kind === 'notFound') return expired(state)
    return { ...state, phase: { ...phase, notice: noticeFor(result.error, now, false) } }
  }
  return { ...state, phase: { kind: 'report', report: result.data } }
}

export function reducer(state: State, action: Action): State {
  const { phase } = state
  switch (action.type) {
    case 'serverAwake':
      return { ...state, serverAwake: true }
    case 'optionsLoaded':
      return { ...state, options: action.options }
    case 'quotaLoaded':
      return { ...state, quota: { kind: 'known', quota: action.quota } }
    case 'quotaHidden':
      return { ...state, quota: { kind: 'hidden' } }

    case 'draftChanged':
      if (phase.kind !== 'configuring') return state
      return { ...state, draft: { ...state.draft, ...action.draft } }

    case 'uploadRequested': {
      const { file } = state.draft
      if (phase.kind !== 'configuring' || file === null) return state
      if (isTooLarge(file)) return { ...state, phase: { kind: 'configuring', notice: { kind: 'message', text: TOO_LARGE_MESSAGE } } }
      return { ...state, phase: { kind: 'uploading', loaded: 0, total: file.size } }
    }
    case 'uploadProgress':
      if (phase.kind !== 'uploading') return state
      return { ...state, phase: { ...phase, loaded: action.loaded, total: action.total } }
    case 'uploadFinished': {
      if (phase.kind !== 'uploading') return state
      const { result } = action
      if (!result.ok) return { ...state, phase: { kind: 'configuring', notice: noticeFor(result.error, action.now, false) } }
      return { ...state, phase: { kind: 'queued', sessionId: result.data.session_id, position: result.data.queue_position, notice: null } }
    }

    case 'sessionPolled':
      return polled(state, action.sessionId, action.result, action.now)
    case 'pollResumed':
      if ((phase.kind !== 'queued' && phase.kind !== 'embedding') || !phase.notice) return state
      return { ...state, phase: { ...phase, notice: null } }

    case 'startRequested':
      if (phase.kind !== 'ready') return state
      return { ...state, phase: { kind: 'preparing', sessionId: phase.sessionId, auto: action.auto } }
    case 'startFinished':
      return started(state, action.sessionId, action.result, action.now)

    case 'answerChanged':
      if (phase.kind !== 'question') return state
      return { ...state, phase: { ...phase, draft: action.text } }
    case 'answerRequested': {
      if (phase.kind !== 'question' || phase.draft.trim() === '') return state
      const { notice: _notice, ...defense } = phase
      return { ...state, phase: { ...defense, kind: 'submitting', auto: action.auto } }
    }
    case 'answerFinished':
      return answered(state, action.sessionId, action.result, action.now)

    case 'reportRequested':
      if (phase.kind !== 'finishing') return state
      return { ...state, phase: { ...phase, notice: null } }
    case 'reportFinished':
      return reported(state, action.sessionId, action.result, action.now)

    case 'restart':
      return { ...state, draft: { ...state.draft, file: null }, phase: { kind: 'configuring', notice: null } }
  }
}
