// The defense flow as one state machine (D6). A pure function with no React and no
// network: requests happen in useFlow.ts, and their results come back here as actions.
// Every action that needs the time carries `now`, so the reducer stays pure.
import type { ApiError, ApiResult } from '../api/client'
import { defaults } from '../api/constraints.gen'
import type {
  AnswerResponse,
  CurrentQuestion,
  Evaluation,
  IngestSummary,
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
import type { PresetId } from '../lib/presets'
import { isTooLarge, TOO_LARGE_MESSAGE } from '../lib/upload'

/** What went wrong, in the terms the screen needs to show it and offer a way on. */
export type Notice =
  | { kind: 'network' }
  | { kind: 'turnFailed' }
  | { kind: 'stillWorking' } // an answer's resends kept getting 409 while the first was running (E13)
  // retryAt: when the one automatic retry fires (epoch ms); null means a "Try again" button only.
  | { kind: 'busy'; message: string; retryAt: number | null }
  | { kind: 'limited'; code: LimitCode | null; limit: number | null; resetsAt: number | null }
  | { kind: 'message'; text: string }

/** The start form. `panel.archetype_keys` holds the Custom choice; a preset's keys are
 * resolved against /panel-options when the upload goes (M10 N7). */
export type Draft = { file: File | null; panel: PanelConfig; preset: PresetId }

export type QuotaView = { kind: 'loading' } | { kind: 'hidden' } | { kind: 'known'; quota: QuotaStatus }

/** How the panel speaks, chosen before the first question (M3b E9). */
export type VoiceChoice = 'panel' | 'browser' | 'none'

/** An answered question, shown with its evaluation and, now that it's answered, its sources (D18). */
export type Turn = { question: QuestionOut; answer: string; evaluation: Evaluation }

type Defense = {
  sessionId: string
  topic: string
  panel: PanelistOut[]
  question: QuestionOut
  draft: string // the answer box; kept through a failed send (D16, 502)
  last: Turn | null
  turns: Turn[] // every answered question, oldest first, for the transcript drawer (M10 N10)
}

export type Phase =
  | { kind: 'configuring'; notice: Notice | null }
  | { kind: 'uploading'; loaded: number; total: number }
  | { kind: 'queued'; sessionId: string; position: number; notice: Notice | null }
  | { kind: 'embedding'; sessionId: string; progress: number; notice: Notice | null }
  | { kind: 'failed'; message: string; scanned: boolean }
  | { kind: 'expired' } // a 404: the session is gone (D16)
  | { kind: 'ready'; sessionId: string; chunkCount: number | null; ingest: IngestSummary | null; notice: Notice | null }
  // auto: this request is the one automatic retry after a busy 503, so no second one follows.
  | { kind: 'preparing'; sessionId: string; ingest: IngestSummary | null; auto: boolean }
  | ({ kind: 'question'; notice: Notice | null } & Defense)
  | ({ kind: 'submitting'; auto: boolean } & Defense)
  | { kind: 'finishing'; sessionId: string; notice: Notice | null }
  | { kind: 'report'; report: ReportResponse }

export type State = {
  serverAwake: boolean // a flag beside the phase, not a phase (D25)
  ttsAvailable: boolean // the server can make panel voices (/status, M3b E6)
  voice: VoiceChoice | null // null until chosen for this defense; never persisted (E9)
  panelDisclosed: boolean // the MiMo disclosure was shown this session (M10 N9)
  options: PanelOptions | null
  quota: QuotaView
  draft: Draft
  phase: Phase
}

export type Action =
  | { type: 'serverAwake'; ttsAvailable: boolean }
  | { type: 'voiceChosen'; voice: VoiceChoice }
  | { type: 'optionsLoaded'; options: PanelOptions }
  | { type: 'quotaLoaded'; quota: QuotaStatus }
  | { type: 'quotaHidden' }
  | { type: 'draftChanged'; draft: Partial<Draft> }
  | { type: 'startNotice'; notice: Notice } // a start-screen problem before any upload (the sample paper didn't load)
  | { type: 'uploadRequested' }
  | { type: 'uploadProgress'; loaded: number; total: number }
  // panel: the config that was sent, archetype keys resolved (M10 N7)
  | { type: 'uploadFinished'; result: ApiResult<SessionCreated>; panel: PanelConfig; now: number }
  | { type: 'sessionPolled'; sessionId: string; result: ApiResult<SessionState>; now: number }
  | { type: 'pollResumed' }
  | { type: 'startRequested'; auto: boolean }
  | { type: 'startFinished'; sessionId: string; result: ApiResult<StartResponse>; now: number }
  | { type: 'answerChanged'; text: string }
  | { type: 'answerRequested'; auto: boolean }
  | { type: 'answerFinished'; sessionId: string; result: ApiResult<AnswerResponse>; now: number }
  // An answer refused as stale, then GET /current's answer (M6 H5).
  | { type: 'resynced'; sessionId: string; stale: ApiError; current: ApiResult<CurrentQuestion>; now: number }
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
    ttsAvailable: false,
    voice: null,
    panelDisclosed: false,
    options: null,
    quota: { kind: 'loading' },
    draft: { file: null, panel: { ...defaults.PanelConfig }, preset: 'recommended' },
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
    case 'conflict':
      return { kind: 'stillWorking' }
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
        limit: error.limit,
        resetsAt: error.retryAfterS !== null ? now + error.retryAfterS * 1000 : null,
      }
    case 'notFound': // callers send a 404 to the expired phase before asking for a notice
    case 'stale':
    case 'alreadyStarted':
    case 'tts': // /tts errors never reach the flow; the voice falls back on its own (E8)
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
      return {
        ...state,
        phase: { kind: 'ready', sessionId, chunkCount: session.chunk_count ?? null, ingest: session.ingest ?? null, notice: null },
      }
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

export const STILL_PREPARING_TEXT = 'The panel is still preparing this defense. Try again in a moment.'

/** "This answer was for an earlier question...": shown after a stale answer moves the screen on (M6 H5). */
export function resyncedText(turn: number): string {
  return `This answer was for an earlier question. You're now on question ${turn}.`
}

function started(state: State, sessionId: string, result: ApiResult<StartResponse>, now: number): State {
  const { phase } = state
  if (!inFlight(phase, 'preparing', sessionId)) return state
  if (!result.ok) {
    if (result.error.kind === 'notFound') return expired(state)
    // A plain 409 means useFlow's resends ran out while the first /start was still running
    // (M6 H4; this replaces D16's "swallowed", which left the screen preparing for good).
    if (result.error.kind === 'conflict') {
      const notice: Notice = { kind: 'message', text: STILL_PREPARING_TEXT }
      return { ...state, phase: { kind: 'ready', sessionId, chunkCount: null, ingest: phase.ingest, notice } }
    }
    const notice = noticeFor(result.error, now, !phase.auto)
    return { ...state, phase: { kind: 'ready', sessionId, chunkCount: null, ingest: phase.ingest, notice } }
  }
  const { panel, question, topic } = result.data
  return { ...state, phase: { kind: 'question', sessionId, topic, panel, question, draft: '', last: null, turns: [], notice: null } }
}

function answered(state: State, sessionId: string, result: ApiResult<AnswerResponse>, now: number): State {
  const { phase } = state
  if (!inFlight(phase, 'submitting', sessionId)) return state
  const { auto, ...defense } = phase
  if (!result.ok) {
    // A 409 here means useFlow's resends ran out while the first request was still
    // running (E13; this replaces D16's "swallowed" for answers).
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
  const turns = [...defense.turns, last]
  return { ...state, phase: { ...defense, kind: 'question', question: next_question, draft: '', last, turns, notice: null } }
}

function resynced(state: State, sessionId: string, stale: ApiError, current: ApiResult<CurrentQuestion>, now: number): State {
  const { phase } = state
  if (!inFlight(phase, 'submitting', sessionId)) return state
  const { auto: _auto, ...defense } = phase
  if (!current.ok) {
    // No way to the open question: show the stale refusal as before (E13).
    if (current.error.kind === 'notFound') return expired(state)
    return { ...state, phase: { ...defense, kind: 'question', notice: noticeFor(stale, now, false) } }
  }
  if (current.data.done) return { ...state, phase: { kind: 'finishing', sessionId, notice: null } }
  const question = current.data.question
  if (question === null) return { ...state, phase: { ...defense, kind: 'question', notice: noticeFor(stale, now, false) } }
  // The typed text stays in the box; the last evaluation was for a question this screen never saw answered.
  const notice: Notice = { kind: 'message', text: resyncedText(question.turn) }
  return { ...state, phase: { ...defense, kind: 'question', question, last: null, notice } }
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
      return { ...state, serverAwake: true, ttsAvailable: action.ttsAvailable }
    case 'voiceChosen':
      // Chosen before the first question, and switchable during the defense (M10 N9).
      if (phase.kind !== 'ready' && phase.kind !== 'question' && phase.kind !== 'submitting') return state
      // Panel voices send question text to MiMo, so they're only offered when the server has them.
      if (action.voice === 'panel' && !state.ttsAvailable) return state
      // Every screen that offers Panel voice shows the disclosure with it, or before it.
      return { ...state, voice: action.voice, panelDisclosed: state.panelDisclosed || action.voice === 'panel' }
    case 'optionsLoaded':
      return { ...state, options: action.options }
    case 'quotaLoaded':
      return { ...state, quota: { kind: 'known', quota: action.quota } }
    case 'quotaHidden':
      return { ...state, quota: { kind: 'hidden' } }

    case 'draftChanged':
      if (phase.kind !== 'configuring') return state
      return { ...state, draft: { ...state.draft, ...action.draft } }
    case 'startNotice':
      if (phase.kind !== 'configuring') return state
      return { ...state, phase: { kind: 'configuring', notice: action.notice } }

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
      return {
        ...state,
        draft: { ...state.draft, panel: action.panel },
        phase: { kind: 'queued', sessionId: result.data.session_id, position: result.data.queue_position, notice: null },
      }
    }

    case 'sessionPolled':
      return polled(state, action.sessionId, action.result, action.now)
    case 'pollResumed':
      if ((phase.kind !== 'queued' && phase.kind !== 'embedding') || !phase.notice) return state
      return { ...state, phase: { ...phase, notice: null } }

    case 'startRequested':
      if (phase.kind !== 'ready') return state
      return { ...state, phase: { kind: 'preparing', sessionId: phase.sessionId, ingest: phase.ingest, auto: action.auto } }
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
    case 'resynced':
      return resynced(state, action.sessionId, action.stale, action.current, action.now)

    case 'reportRequested':
      if (phase.kind !== 'finishing') return state
      return { ...state, phase: { ...phase, notice: null } }
    case 'reportFinished':
      return reported(state, action.sessionId, action.result, action.now)

    case 'restart': // a new document is a new defense: the voice is chosen again
      return {
        ...state,
        voice: null,
        panelDisclosed: false,
        draft: { ...state.draft, file: null },
        phase: { kind: 'configuring', notice: null },
      }
  }
}
