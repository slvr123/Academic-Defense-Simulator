import { describe, expect, it } from 'vitest'
import { fakeServer } from '../test/fakeServer'
import { quota } from '../test/fixtures'
import { createSession, fetchTts, getQuota, getStatus, parseRetryAfter, submitAnswer, toApiError } from './client'
import type { PanelConfig } from './types'

const panel: PanelConfig = {
  defense_type: 'thesis',
  other_subtype: null,
  domain: null,
  topic: null,
  archetype_keys: null,
  starting_difficulty: 2,
}

describe('toApiError maps each status to the error the UI branches on', () => {
  it.each([
    [404, 'notFound'],
    [409, 'conflict'],
    [413, 'tooLarge'],
    [422, 'invalid'],
    [502, 'turnFailed'],
    [503, 'busy'],
    [429, 'limited'],
    [500, 'unexpected'],
    [418, 'unexpected'],
  ])('%i -> %s', (status, kind) => {
    expect(toApiError(status, { detail: 'x' }, null).kind).toBe(kind)
  })

  it('keeps the server message verbatim', () => {
    expect(toApiError(413, { detail: 'That PDF has 300 pages; the limit is 250.' }, null)).toEqual({
      kind: 'tooLarge',
      message: 'That PDF has 300 pages; the limit is 250.',
    })
  })

  it('joins a 422 list of validation errors into their messages', () => {
    const body = { detail: [{ msg: 'Value error, archetype keys must not repeat' }, { msg: 'Field required' }] }
    expect(toApiError(422, body, null)).toEqual({
      kind: 'invalid',
      message: 'Value error, archetype keys must not repeat\nField required',
    })
  })

  it('falls back to a readable message when the body has no detail', () => {
    expect(toApiError(500, undefined, null)).toEqual({
      kind: 'unexpected',
      message: 'The server answered with an unexpected error (500).',
    })
  })

  it.each([
    ['per_ip_limit', 'per_ip_limit'],
    ['daily_cap', 'daily_cap'],
    ['something_new', null],
    [undefined, null],
  ])('a 429 with code %s is read as %s', (code, expected) => {
    const error = toApiError(429, { detail: 'used up', code }, '3600')
    expect(error).toEqual({ kind: 'limited', code: expected, message: 'used up', retryAfterS: 3600 })
  })

  it('tells a stale_question 409 from a plain one (E13)', () => {
    expect(toApiError(409, { detail: 'moved on', code: 'stale_question' }, null)).toEqual({ kind: 'stale', message: 'moved on' })
    expect(toApiError(409, { detail: 'still running' }, null)).toEqual({ kind: 'conflict', message: 'still running' })
  })

  it.each([
    [429, 'tts_limit'],
    [502, 'tts_failed'],
    [503, 'tts_unavailable'],
  ] as const)('reads a %i with code %s as a tts error, not as a limit, a turn or busy (E3)', (status, code) => {
    expect(toApiError(status, { detail: 'no voice', code }, '60')).toEqual({ kind: 'tts', code, message: 'no voice' })
  })

  it('reads Retry-After on a 503, and null without it', () => {
    expect(toApiError(503, { detail: 'busy' }, '60')).toEqual({ kind: 'busy', message: 'busy', retryAfterS: 60 })
    expect(toApiError(503, { detail: 'busy' }, null)).toEqual({ kind: 'busy', message: 'busy', retryAfterS: null })
  })
})

describe('parseRetryAfter', () => {
  it.each([
    ['60', 60],
    [' 5 ', 5],
    ['0', 0],
    ['Wed, 21 Oct 2026 07:28:00 GMT', null],
    ['-1', null],
    [null, null],
  ])('%s -> %s', (header, expected) => {
    expect(parseRetryAfter(header)).toBe(expected)
  })
})

describe('fetch requests', () => {
  it('returns the typed body from the configured base URL', async () => {
    const server = fakeServer()
    server.on('GET /quota', { status: 200, body: quota(7) })
    expect(await getQuota()).toEqual({ ok: true, data: quota(7) })
    expect(server.calls).toEqual(['GET /quota'])
  })

  it('maps a thrown fetch to a network error', async () => {
    fakeServer().on('GET /quota', 'network')
    expect(await getQuota()).toEqual({ ok: false, error: { kind: 'network' } })
  })

  it('sends an answer as JSON and reads the Retry-After header of an error', async () => {
    const server = fakeServer()
    let sent: unknown
    server.on('POST /sessions/s%201/answers', (body) => {
      sent = body
      return { status: 503, body: { detail: 'The panel is busy right now.' }, headers: { 'Retry-After': '60' } }
    })
    const result = await submitAnswer('s 1', 'My answer', 1)
    expect(sent).toEqual({ text: 'My answer', turn: 1 })
    expect(result).toEqual({ ok: false, error: { kind: 'busy', message: 'The panel is busy right now.', retryAfterS: 60 } })
  })
})

describe('/status (E2: EasyPrivacy blocks /health on onrender.com)', () => {
  it('asks /status, never /health', async () => {
    const server = fakeServer()
    const body = { status: 'ok', model_loaded: true, tts_available: false }
    server.on('GET /status', { status: 200, body })
    expect(await getStatus()).toEqual({ ok: true, data: body })
    expect(server.calls).toEqual(['GET /status'])
  })
})

describe('fetchTts reads binary audio and can be cancelled (E3, E7)', () => {
  const wav = new Uint8Array([0x52, 0x49, 0x46, 0x46, 1, 2, 3, 4]) // "RIFF" + 4 bytes

  it('sends the session and turn, never text, and returns the bytes as a Blob', async () => {
    const server = fakeServer()
    let sent: unknown
    server.on('POST /tts', (body) => {
      sent = body
      return { status: 200, bytes: wav, headers: { 'Content-Type': 'audio/wav' } }
    })
    const result = await fetchTts('abc', 2, new AbortController().signal)
    expect(sent).toEqual({ session_id: 'abc', turn: 2 })
    if (!result.ok) throw new Error('expected audio')
    expect(result.data.type).toBe('audio/wav')
    expect(new Uint8Array(await result.data.arrayBuffer())).toEqual(wav)
  })

  it('maps a coded failure', async () => {
    fakeServer().on('POST /tts', { status: 502, body: { detail: 'no voice', code: 'tts_failed' } })
    expect(await fetchTts('abc', 1, new AbortController().signal)).toEqual({
      ok: false,
      error: { kind: 'tts', code: 'tts_failed', message: 'no voice' },
    })
  })

  it('resolves a cancelled request as a network error with the signal aborted', async () => {
    const server = fakeServer()
    server.on('POST /tts', () => new Promise<never>(() => {})) // never answers
    const controller = new AbortController()
    const pending = fetchTts('abc', 1, controller.signal)
    controller.abort()
    expect(await pending).toEqual({ ok: false, error: { kind: 'network' } })
    expect(controller.signal.aborted).toBe(true)
  })
})

describe('createSession uploads with XHR', () => {
  const file = new File(['%PDF-1.7'], 'thesis.pdf', { type: 'application/pdf' })

  it('reports progress and returns the 202 body', async () => {
    const server = fakeServer()
    const progress: number[][] = []
    const pending = createSession(file, panel, (loaded, total) => progress.push([loaded, total]))
    const xhr = server.upload.current
    if (xhr === null) throw new Error('no upload sent')
    expect(xhr.form?.get('panel')).toBe(JSON.stringify(panel))
    expect(xhr.form?.get('file')).toBeInstanceOf(File)
    xhr.progress(5, 10)
    xhr.progress(10, 10)
    xhr.respond(202, { session_id: 'abc', status: 'queued', queue_position: 2 })
    expect(await pending).toEqual({ ok: true, data: { session_id: 'abc', status: 'queued', queue_position: 2 } })
    expect(progress).toEqual([
      [5, 10],
      [10, 10],
    ])
    expect(server.calls).toEqual(['POST /sessions'])
  })

  it('maps an error status with its headers', async () => {
    const server = fakeServer()
    const pending = createSession(file, panel, () => {})
    server.upload.current?.respond(429, { detail: 'used up', code: 'daily_cap' }, { 'Retry-After': '120' })
    expect(await pending).toEqual({
      ok: false,
      error: { kind: 'limited', code: 'daily_cap', message: 'used up', retryAfterS: 120 },
    })
  })

  it('maps a dropped connection to a network error', async () => {
    const server = fakeServer()
    const pending = createSession(file, panel, () => {})
    server.upload.current?.fail()
    expect(await pending).toEqual({ ok: false, error: { kind: 'network' } })
  })
})
