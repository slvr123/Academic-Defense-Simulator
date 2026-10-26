import { describe, expect, it } from 'vitest'
import type { ApiError, ApiResult } from '../api/client'
import type { SessionState } from '../api/types'
import { evaluation, lastAnswer, nextQuestion, panelOptions, pdf, quota, report, startResponse } from '../test/fixtures'
import { type Action, initialState, noticeFor, type Phase, reducer, type State } from './flow'

const NOW = Date.parse('2026-10-22T05:00:00Z')
const fail = <T,>(error: ApiError): ApiResult<T> => ({ ok: false, error })
const ok = <T,>(data: T): ApiResult<T> => ({ ok: true, data })

function at(phase: Phase, extra: Partial<State> = {}): State {
  return { ...initialState(), ...extra, phase }
}

function run(state: State, ...actions: Action[]): State {
  return actions.reduce(reducer, state)
}

const S = 'session-1'
const question: Phase = {
  kind: 'question',
  sessionId: S,
  topic: startResponse.topic,
  panel: startResponse.panel,
  question: startResponse.question,
  draft: 'My answer',
  last: null,
  notice: null,
}
const { notice: _notice, ...defense } = question
const submitting: Phase = { ...defense, kind: 'submitting', auto: false }

describe('initial state', () => {
  it('starts on the config screen with the server not yet awake and PanelConfig defaults', () => {
    const state = initialState()
    expect(state.phase).toEqual({ kind: 'configuring', notice: null })
    expect(state.serverAwake).toBe(false)
    expect(state.quota).toEqual({ kind: 'loading' })
    expect(state.draft.panel).toEqual({
      defense_type: 'thesis',
      other_subtype: null,
      domain: null,
      topic: null,
      archetype_keys: null,
      starting_difficulty: 2,
    })
  })
})

describe('page load', () => {
  it('records the server awake, the quota, the rosters, and hides the quota on failure', () => {
    let state = run(initialState(), { type: 'serverAwake', ttsAvailable: true }, { type: 'optionsLoaded', options: panelOptions })
    expect(state.serverAwake).toBe(true)
    expect(state.ttsAvailable).toBe(true)
    expect(state.options).toBe(panelOptions)
    expect(run(state, { type: 'quotaLoaded', quota: quota(7) }).quota).toEqual({ kind: 'known', quota: quota(7) })
    state = run(state, { type: 'quotaHidden' })
    expect(state.quota).toEqual({ kind: 'hidden' })
  })
})

describe('config and upload', () => {
  const withFile = at({ kind: 'configuring', notice: null }, { draft: { ...initialState().draft, file: pdf() } })

  it('edits the draft only while configuring', () => {
    const file = pdf()
    expect(run(initialState(), { type: 'draftChanged', draft: { file } }).draft.file).toBe(file)
    expect(run(at({ kind: 'expired' }), { type: 'draftChanged', draft: { file } }).draft.file).toBeNull()
  })

  it('ignores a submit with no file', () => {
    expect(run(initialState(), { type: 'uploadRequested' }).phase.kind).toBe('configuring')
  })

  it('refuses a file over 30 MB before uploading', () => {
    const big = pdf()
    Object.defineProperty(big, 'size', { value: 30 * 1024 * 1024 + 1 })
    const state = run(at({ kind: 'configuring', notice: null }, { draft: { ...initialState().draft, file: big } }), {
      type: 'uploadRequested',
    })
    expect(state.phase).toEqual({ kind: 'configuring', notice: { kind: 'message', text: 'That PDF is larger than the 30 MB limit.' } })
  })

  it('uploads, tracks progress and queues', () => {
    const state = run(
      withFile,
      { type: 'uploadRequested' },
      { type: 'uploadProgress', loaded: 512, total: 1024 },
    )
    expect(state.phase).toEqual({ kind: 'uploading', loaded: 512, total: 1024 })
    const queued = run(state, {
      type: 'uploadFinished',
      result: ok({ session_id: S, status: 'queued', queue_position: 2 }),
      now: NOW,
    })
    expect(queued.phase).toEqual({ kind: 'queued', sessionId: S, position: 2, notice: null })
  })

  it('returns to config with a notice when the upload fails, keeping the file', () => {
    const state = run(withFile, { type: 'uploadRequested' }, {
      type: 'uploadFinished',
      result: fail({ kind: 'invalid', message: "That file isn't a PDF. Upload a .pdf document." }),
      now: NOW,
    })
    expect(state.phase).toEqual({ kind: 'configuring', notice: { kind: 'message', text: "That file isn't a PDF. Upload a .pdf document." } })
    expect(state.draft.file).not.toBeNull()
  })

  it('never schedules an automatic retry for an upload, even with Retry-After', () => {
    const state = run(withFile, { type: 'uploadRequested' }, {
      type: 'uploadFinished',
      result: fail({ kind: 'busy', message: 'busy', retryAfterS: 60 }),
      now: NOW,
    })
    expect(state.phase).toEqual({ kind: 'configuring', notice: { kind: 'busy', message: 'busy', retryAt: null } })
  })

  it('ignores progress and results when not uploading', () => {
    const state = initialState()
    expect(run(state, { type: 'uploadProgress', loaded: 1, total: 2 })).toBe(state)
    expect(run(state, { type: 'uploadFinished', result: ok({ session_id: S, status: 'queued', queue_position: 1 }), now: NOW })).toBe(state)
  })
})

describe('polling', () => {
  const queued = at({ kind: 'queued', sessionId: S, position: 2, notice: null })
  const poll = (result: SessionState | ApiError, sessionId = S): Action => ({
    type: 'sessionPolled',
    sessionId,
    result: 'kind' in result ? fail(result) : ok(result),
    now: NOW,
  })

  it('moves through queued, embedding and ready', () => {
    let state = run(queued, poll({ status: 'queued', queue_position: 1 }))
    expect(state.phase).toEqual({ kind: 'queued', sessionId: S, position: 1, notice: null })
    state = run(state, poll({ status: 'embedding', progress: 0.4 }))
    expect(state.phase).toEqual({ kind: 'embedding', sessionId: S, progress: 0.4, notice: null })
    state = run(state, poll({ status: 'ready', chunk_count: 92, defense: 'not_started' }))
    expect(state.phase).toEqual({ kind: 'ready', sessionId: S, chunkCount: 92, notice: null })
  })

  it('reads a scanned PDF from error_code, not the text', () => {
    const scanned = run(queued, poll({ status: 'failed', error: 'any wording at all', error_code: 'scanned_pdf' }))
    expect(scanned.phase).toEqual({ kind: 'failed', message: 'any wording at all', scanned: true })
    const other = run(queued, poll({ status: 'failed', error: 'This PDF looks scanned: but no code' }))
    expect(other.phase).toEqual({ kind: 'failed', message: 'This PDF looks scanned: but no code', scanned: false })
  })

  it('sends a 404 to the expired phase and clears the file', () => {
    const state = run(at(queued.phase, { draft: { ...initialState().draft, file: pdf() } }), poll({ kind: 'notFound', message: 'gone' }))
    expect(state.phase).toEqual({ kind: 'expired' })
    expect(state.draft.file).toBeNull()
  })

  it('pauses on a network error until resumed', () => {
    const paused = run(queued, poll({ kind: 'network' }))
    expect(paused.phase).toEqual({ ...queued.phase, notice: { kind: 'network' } })
    expect(run(paused, poll({ status: 'ready', chunk_count: 3 }))).toBe(paused) // a late result is dropped
    expect(run(paused, { type: 'pollResumed' }).phase).toEqual(queued.phase)
  })

  it('drops results for another session', () => {
    expect(run(queued, poll({ status: 'ready' }, 'other-session'))).toBe(queued)
  })
})

describe('starting the defense', () => {
  const ready = at({ kind: 'ready', sessionId: S, chunkCount: 8, notice: null })
  const startWith = (result: ApiResult<typeof startResponse>, auto = false) =>
    run(ready, { type: 'startRequested', auto }, { type: 'startFinished', sessionId: S, result, now: NOW })

  it('prepares, then opens the first question', () => {
    expect(run(ready, { type: 'startRequested', auto: false }).phase).toEqual({ kind: 'preparing', sessionId: S, auto: false })
    expect(startWith(ok(startResponse)).phase).toEqual({ ...question, draft: '' })
  })

  it('swallows a 409 and keeps preparing', () => {
    expect(startWith(fail({ kind: 'conflict', message: 'running' })).phase).toEqual({ kind: 'preparing', sessionId: S, auto: false })
  })

  it('schedules one automatic retry for a busy 503 with Retry-After, and none after it', () => {
    const busy = fail<typeof startResponse>({ kind: 'busy', message: 'The panel is busy right now.', retryAfterS: 60 })
    expect(startWith(busy).phase).toEqual({
      kind: 'ready',
      sessionId: S,
      chunkCount: null,
      notice: { kind: 'busy', message: 'The panel is busy right now.', retryAt: NOW + 60_000 },
    })
    expect(startWith(busy, true).phase).toMatchObject({ notice: { kind: 'busy', retryAt: null } })
  })

  it('keeps a 503 without Retry-After to a button', () => {
    const state = startWith(fail({ kind: 'busy', message: "The demo's usage counter can't be reached", retryAfterS: null }))
    expect(state.phase).toMatchObject({ notice: { kind: 'busy', retryAt: null } })
  })

  it.each([
    ['per_ip_limit' as const],
    ['daily_cap' as const],
    [null],
  ])('carries the 429 code %s and its reset time', (code) => {
    const state = startWith(fail({ kind: 'limited', code, message: 'm', retryAfterS: 3600 }))
    expect(state.phase).toMatchObject({ kind: 'ready', notice: { kind: 'limited', code, resetsAt: NOW + 3_600_000 } })
  })

  it('maps 502, 422 and a network error to their notices', () => {
    expect(startWith(fail({ kind: 'turnFailed', message: 'm' })).phase).toMatchObject({ notice: { kind: 'turnFailed' } })
    expect(startWith(fail({ kind: 'invalid', message: 'This document has 2 passage(s), too few' })).phase).toMatchObject({
      notice: { kind: 'message', text: 'This document has 2 passage(s), too few' },
    })
    expect(startWith(fail({ kind: 'network' })).phase).toMatchObject({ notice: { kind: 'network' } })
  })

  it('sends a 404 to expired', () => {
    expect(startWith(fail({ kind: 'notFound', message: 'gone' })).phase).toEqual({ kind: 'expired' })
  })
})

describe('answering', () => {
  const finish = (result: ApiResult<typeof nextQuestion>, auto = false) =>
    run(at(question), { type: 'answerRequested', auto }, { type: 'answerFinished', sessionId: S, result, now: NOW })

  it('edits the answer and ignores a blank send', () => {
    const blank = at({ ...question, draft: '   ' })
    expect(run(blank, { type: 'answerRequested', auto: false })).toBe(blank)
    expect(run(at(question), { type: 'answerChanged', text: 'New' }).phase).toMatchObject({ draft: 'New' })
  })

  it('submits, then shows the evaluation with the answered question and opens the next', () => {
    expect(run(at(question), { type: 'answerRequested', auto: false }).phase).toEqual(submitting)
    expect(finish(ok(nextQuestion)).phase).toEqual({
      ...question,
      question: nextQuestion.next_question,
      draft: '',
      last: { question: startResponse.question, answer: 'My answer', evaluation },
    })
  })

  it('goes to finishing after the last answer, then to the report', () => {
    const finishing = finish(ok(lastAnswer))
    expect(finishing.phase).toEqual({ kind: 'finishing', sessionId: S, notice: null })
    const done = run(finishing, { type: 'reportFinished', sessionId: S, result: ok(report), now: NOW })
    expect(done.phase).toEqual({ kind: 'report', report })
  })

  it('keeps the answer text after a 502 so the same text goes again', () => {
    expect(finish(fail({ kind: 'turnFailed', message: 'm' })).phase).toEqual({ ...question, notice: { kind: 'turnFailed' } })
  })

  it('offers Try again once the resends of a 409 ran out, keeping the answer (E13, replaces D16 "swallowed")', () => {
    expect(finish(fail({ kind: 'conflict', message: 'm' })).phase).toEqual({ ...question, notice: { kind: 'stillWorking' } })
  })

  it('shows a stale_question 409 verbatim, keeping the answer (E13)', () => {
    const text = "That answer is for a question that isn't open any more."
    expect(finish(fail({ kind: 'stale', message: text })).phase).toEqual({ ...question, notice: { kind: 'message', text } })
  })

  it('schedules one automatic retry for a busy 503, none after the retry itself', () => {
    const busy = fail<typeof nextQuestion>({ kind: 'busy', message: 'busy', retryAfterS: 60 })
    expect(finish(busy).phase).toMatchObject({ kind: 'question', draft: 'My answer', notice: { kind: 'busy', retryAt: NOW + 60_000 } })
    expect(finish(busy, true).phase).toMatchObject({ notice: { kind: 'busy', retryAt: null } })
  })

  it('treats a missing next question as an error the user can resend', () => {
    const state = finish(ok({ evaluation, next_question: null, done: false }))
    expect(state.phase).toMatchObject({ kind: 'question', draft: 'My answer', notice: { kind: 'message' } })
  })

  it('sends a 404 to expired', () => {
    expect(finish(fail({ kind: 'notFound', message: 'gone' })).phase).toEqual({ kind: 'expired' })
  })
})

describe('the voice choice (M3b E9)', () => {
  const ready = (ttsAvailable: boolean) => at({ kind: 'ready', sessionId: S, chunkCount: 8, notice: null }, { ttsAvailable })
  const choose = (voice: 'panel' | 'browser' | 'none'): Action => ({ type: 'voiceChosen', voice })

  it('is made on the ready screen and kept in state', () => {
    expect(initialState().voice).toBeNull()
    expect(run(ready(true), choose('panel')).voice).toBe('panel')
    expect(run(ready(false), choose('browser')).voice).toBe('browser')
    expect(run(ready(false), choose('none')).voice).toBe('none')
  })

  it('refuses panel voices when the server has none (E6)', () => {
    const state = ready(false)
    expect(run(state, choose('panel'))).toBe(state)
  })

  it('can only be made before the defense starts', () => {
    const state = at(question, { ttsAvailable: true })
    expect(run(state, choose('panel'))).toBe(state)
  })

  it('lasts through the defense and is asked again for the next document', () => {
    const chosen = run(ready(true), choose('browser'), { type: 'startRequested', auto: false })
    expect(chosen.voice).toBe('browser')
    expect(run(chosen, { type: 'restart' }).voice).toBeNull()
  })
})

describe('the report', () => {
  const finishing = at({ kind: 'finishing', sessionId: S, notice: null })

  it('pauses on a failed fetch and retries on request', () => {
    const failed = run(finishing, { type: 'reportFinished', sessionId: S, result: fail({ kind: 'network' }), now: NOW })
    expect(failed.phase).toEqual({ kind: 'finishing', sessionId: S, notice: { kind: 'network' } })
    expect(run(failed, { type: 'reportRequested' }).phase).toEqual(finishing.phase)
  })

  it('restarts with the file cleared and the panel choices kept', () => {
    const panel = { ...initialState().draft.panel, starting_difficulty: 4 }
    const state = run(at({ kind: 'report', report }, { draft: { file: pdf(), panel } }), { type: 'restart' })
    expect(state.phase).toEqual({ kind: 'configuring', notice: null })
    expect(state.draft).toEqual({ file: null, panel })
  })
})

describe('noticeFor', () => {
  it('keeps limited without Retry-After to no reset time', () => {
    expect(noticeFor({ kind: 'limited', code: 'daily_cap', message: 'm', retryAfterS: null }, NOW, true)).toEqual({
      kind: 'limited',
      code: 'daily_cap',
      resetsAt: null,
    })
  })

  it('shows 413 and unexpected errors verbatim', () => {
    expect(noticeFor({ kind: 'tooLarge', message: 'That PDF has 300 pages; the limit is 250.' }, NOW, true)).toEqual({
      kind: 'message',
      text: 'That PDF has 300 pages; the limit is 250.',
    })
    expect(noticeFor({ kind: 'unexpected', message: 'x' }, NOW, true)).toEqual({ kind: 'message', text: 'x' })
  })
})
