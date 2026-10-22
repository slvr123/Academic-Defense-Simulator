import { describe, expect, it } from 'vitest'
import { fakeServer } from '../test/fakeServer'
import { quota } from '../test/fixtures'
import { createSession, getQuota, parseRetryAfter, submitAnswer, toApiError } from './client'
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
    const result = await submitAnswer('s 1', 'My answer')
    expect(sent).toEqual({ text: 'My answer' })
    expect(result).toEqual({ ok: false, error: { kind: 'busy', message: 'The panel is busy right now.', retryAfterS: 60 } })
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
