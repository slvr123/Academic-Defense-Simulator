// Panel voices through the real App with a scripted network (M3b E7 to E11): the
// gate, MiMo playback, the browser-voice fallback, discarding late audio, autoplay
// being blocked, and the speaking indicator.
import { act, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FakeServer, Reply } from './test/fakeServer'
import { nextQuestion, report, startResponse } from './test/fixtures'
import { advance, defaultServer, deferred, renderApp, startDefense, uploadAccepted } from './test/renderApp'
import { fakeAudio, fakeSpeech, restoreObjectUrls, WAV } from './test/voiceFakes'

// M6 H7: App-level tests drive the whole UI with real-time polling, so a slow machine
// needs more than vitest's 5 s default. No retries: a failing test still fails.
vi.setConfig({ testTimeout: 15_000 })

type User = ReturnType<typeof renderApp>

const Q1 = 'How did you validate your survey instrument?'
const Q2 = 'Why thirty respondents?'
const TTS = 'POST /tts'
const ANSWERS = 'POST /sessions/abc/answers'
const CLIP: Reply = { status: 200, bytes: WAV, headers: { 'Content-Type': 'audio/wav' } }

afterEach(restoreObjectUrls)

function voiceServer(): FakeServer {
  const server = defaultServer()
  server.on('GET /sessions/abc', { status: 200, body: { status: 'ready', chunk_count: 8, defense: 'not_started' } })
  server.on('POST /sessions/abc/start', { status: 200, body: startResponse })
  server.on(ANSWERS, { status: 200, body: nextQuestion })
  server.on('GET /sessions/abc/report', { status: 200, body: report })
  server.on(TTS, CLIP)
  return server
}

async function toReady(server: FakeServer, user: User) {
  await uploadAccepted(server, user)
  await advance(2000)
  await screen.findByText('Your document is ready')
}

async function toQuestion(server: FakeServer, user: User, voice: RegExp) {
  await toReady(server, user)
  await startDefense(user, voice)
  await screen.findByText(Q1)
}

const ttsBodies = (bodies: unknown[]) => (server: FakeServer) =>
  server.on(TTS, (body) => {
    bodies.push(body)
    return CLIP
  })

const speakingAvatars = () => document.querySelectorAll('img[data-speaking]').length

describe('the gate (E9)', () => {
  it('offers three choices and keeps Start disabled until one is made', async () => {
    fakeAudio()
    fakeSpeech()
    const server = voiceServer()
    const user = renderApp()
    await toReady(server, user)
    expect(screen.getByRole('radio', { name: /^Panel voices/ })).toHaveAccessibleName(/Xiaomi's Mimo API/)
    // Measured in production on 2026-10-27 (M7 J6).
    expect(screen.getByRole('radio', { name: /^Panel voices/ })).toHaveAccessibleName(/slower, about 7–15 seconds per question/)
    expect(screen.getByRole('radio', { name: /^Browser voice/ })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /^No voice/ })).toBeInTheDocument()
    expect(screen.getByText('This choice is not saved between sessions.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start the defense' })).toBeDisabled()
    await user.click(screen.getByRole('radio', { name: /^Browser voice/ }))
    expect(screen.getByRole('button', { name: 'Start the defense' })).toBeEnabled()
  })

  it('hides panel voices when the server has no MiMo key (E6)', async () => {
    const server = voiceServer()
    server.on('GET /status', { status: 200, body: { status: 'ok', model_loaded: true, tts_available: false } })
    const user = renderApp()
    await toReady(server, user)
    expect(screen.queryByRole('radio', { name: /^Panel voices/ })).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /^Browser voice/ })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /^No voice/ })).toBeInTheDocument()
  })
})

describe('panel voices (MiMo)', () => {
  it('requests the clip as the question arrives, shows it loading, then plays it with the speaking ring', async () => {
    const audio = fakeAudio()
    const speech = fakeSpeech()
    const server = voiceServer()
    const clip = deferred<Reply>()
    const bodies: unknown[] = []
    server.on(TTS, (body) => {
      bodies.push(body)
      return clip.promise
    })
    const user = renderApp()
    await toQuestion(server, user, /^Panel voices/)

    expect(bodies.at(-1)).toEqual({ session_id: 'abc', turn: 1, part: 0 }) // never the text
    expect(screen.getByText('Voice loading')).toBeInTheDocument()
    expect(audio.plays).toEqual([])

    await act(async () => clip.resolve(CLIP))
    await vi.waitFor(() => expect(audio.plays).toEqual(['blob:clip-1']))
    expect(audio.created).toHaveBeenCalledTimes(1) // the cancelled StrictMode request made no URL
    expect(screen.queryByText('Voice loading')).not.toBeInTheDocument()

    act(() => audio.fire('playing'))
    expect(screen.getByText('Speaking')).toBeInTheDocument()
    expect(speakingAvatars()).toBe(2) // the panel strip and the question card

    act(() => audio.fire('ended'))
    expect(screen.queryByText('Speaking')).not.toBeInTheDocument()
    expect(speakingAvatars()).toBe(0)

    await user.click(screen.getByRole('button', { name: /Replay/ }))
    expect(audio.plays).toEqual(['blob:clip-1', 'blob:clip-1']) // replayed from the cache, no new request
    expect(speech.spoken()).toEqual([])
  })

  it('cancels and revokes the last clip when the turn moves on, and asks for the next one', async () => {
    const audio = fakeAudio()
    fakeSpeech()
    const server = voiceServer()
    const bodies: unknown[] = []
    ttsBodies(bodies)(server)
    const user = renderApp()
    await toQuestion(server, user, /^Panel voices/)
    await vi.waitFor(() => expect(audio.plays).toEqual(['blob:clip-1']))

    await user.type(screen.getByLabelText('Your answer'), 'Thirty respondents.')
    await user.click(screen.getByRole('button', { name: 'Send answer' }))
    await screen.findByText(Q2)
    await vi.waitFor(() => expect(audio.plays).toEqual(['blob:clip-1', 'blob:clip-2']))
    expect(audio.revoked).toContain('blob:clip-1')
    expect(bodies.at(-1)).toEqual({ session_id: 'abc', turn: 2, part: 0 })
  })

  it('discards a clip that arrives after the answer was sent (E7)', async () => {
    const audio = fakeAudio()
    const speech = fakeSpeech()
    const server = voiceServer()
    const clip = deferred<Reply>()
    server.on(TTS, () => clip.promise)
    const answered = deferred<Reply>()
    server.on(ANSWERS, () => answered.promise)
    const user = renderApp()
    await toQuestion(server, user, /^Panel voices/)

    await user.type(screen.getByLabelText('Your answer'), 'Thirty respondents.')
    await user.click(screen.getByRole('button', { name: 'Send answer' }))
    expect(screen.getByText('The panel is weighing your answer.')).toBeInTheDocument()
    await act(async () => clip.resolve(CLIP))
    await advance(2000)
    expect(audio.plays).toEqual([])
    expect(audio.created).not.toHaveBeenCalled()
    expect(speech.spoken()).toEqual([])
    expect(screen.queryByText('Voice loading')).not.toBeInTheDocument()

    await act(async () => answered.resolve({ status: 200, body: nextQuestion }))
    expect(await screen.findByText(Q2)).toBeInTheDocument()
  })

  it.each([
    ['a tts_failed 502', { status: 502, body: { detail: 'no voice', code: 'tts_failed' } }],
    ['a tts_limit 429', { status: 429, body: { detail: 'no voice', code: 'tts_limit' } }],
    ['a tts_unavailable 503', { status: 503, body: { detail: 'no voice', code: 'tts_unavailable' } }],
    ['a network failure', 'network'],
  ] satisfies [string, Reply][])('falls back to the browser voice silently on %s (E8)', async (_name, reply) => {
    const audio = fakeAudio()
    const speech = fakeSpeech()
    const server = voiceServer()
    server.on(TTS, reply)
    const user = renderApp()
    await toQuestion(server, user, /^Panel voices/)
    await vi.waitFor(() => expect(speech.spoken().map((u) => u.text)).toContain(Q1))
    expect(audio.plays).toEqual([])
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('falls back to the browser voice when the browser can’t play the clip', async () => {
    const audio = fakeAudio()
    const speech = fakeSpeech()
    const server = voiceServer()
    const user = renderApp()
    await toQuestion(server, user, /^Panel voices/)
    await vi.waitFor(() => expect(audio.plays).toEqual(['blob:clip-1']))
    act(() => audio.fire('error'))
    await vi.waitFor(() => expect(speech.spoken().map((u) => u.text)).toEqual([Q1]))
  })

  it('shows a play button when autoplay is blocked, and plays from it (E10)', async () => {
    const audio = fakeAudio()
    fakeSpeech()
    audio.refuseAutoplay(true)
    const server = voiceServer()
    const user = renderApp()
    await toQuestion(server, user, /^Panel voices/)
    const play = await screen.findByRole('button', { name: /Play the question/ })
    audio.refuseAutoplay(false)
    await user.click(play)
    expect(audio.plays).toEqual(['blob:clip-1', 'blob:clip-1'])
    act(() => audio.fire('playing'))
    expect(screen.getByText('Speaking')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Play the question/ })).not.toBeInTheDocument()
  })
})

describe('panel voices in parts (M6 H9)', () => {
  const partReply = (parts: number): Reply => ({
    status: 200,
    bytes: WAV,
    headers: { 'Content-Type': 'audio/wav', 'X-TTS-Parts': String(parts) },
  })
  const partsAsked = (bodies: unknown[]) =>
    bodies.map((body) => (typeof body === 'object' && body !== null ? (body as { part?: number }).part : undefined))

  it('plays the parts in order, fetching the next one while the current one plays', async () => {
    const audio = fakeAudio()
    const speech = fakeSpeech()
    const server = voiceServer()
    const bodies: unknown[] = []
    server.on(TTS, (body) => {
      bodies.push(body)
      return partReply(3)
    })
    const user = renderApp()
    await toQuestion(server, user, /^Panel voices/)

    await vi.waitFor(() => expect(audio.plays).toHaveLength(1))
    // Part 1 is already asked for while part 0 plays (StrictMode's cancelled request is part 0 too).
    await vi.waitFor(() => expect(partsAsked(bodies)).toContain(1))
    expect(partsAsked(bodies)).not.toContain(2)
    act(() => audio.fire('playing'))
    expect(screen.getByText('Speaking')).toBeInTheDocument()

    act(() => audio.fire('ended'))
    await vi.waitFor(() => expect(audio.plays).toHaveLength(2))
    expect(screen.getByText('Speaking')).toBeInTheDocument() // no gap shown between parts
    await vi.waitFor(() => expect(partsAsked(bodies)).toContain(2))
    act(() => audio.fire('ended'))
    await vi.waitFor(() => expect(audio.plays).toHaveLength(3))
    act(() => audio.fire('ended'))
    await vi.waitFor(() => expect(screen.queryByText('Speaking')).not.toBeInTheDocument())
    expect(new Set(audio.plays).size).toBe(3) // three different clips

    const asked = bodies.length
    await user.click(screen.getByRole('button', { name: /Replay/ }))
    expect(audio.plays.at(-1)).toBe(audio.plays[0]) // Replay starts again at part 0, from the cache
    act(() => audio.fire('ended'))
    await vi.waitFor(() => expect(audio.plays).toHaveLength(5))
    expect(bodies).toHaveLength(asked) // no new requests
    expect(speech.spoken()).toEqual([])
  })

  it('stops quietly when a later part fails, with no browser voice', async () => {
    const audio = fakeAudio()
    const speech = fakeSpeech()
    const server = voiceServer()
    server.on(TTS, (body) =>
      partsAsked([body])[0] === 0 ? partReply(2) : { status: 502, body: { detail: 'no voice', code: 'tts_failed' } },
    )
    const user = renderApp()
    await toQuestion(server, user, /^Panel voices/)
    await vi.waitFor(() => expect(audio.plays).toHaveLength(1))
    act(() => audio.fire('playing'))
    act(() => audio.fire('ended'))
    await vi.waitFor(() => expect(screen.queryByText('Speaking')).not.toBeInTheDocument())
    await advance(1000)
    expect(audio.plays).toHaveLength(1)
    expect(speech.spoken()).toEqual([])
  })

  it('revokes every part it made when the turn moves on', async () => {
    const audio = fakeAudio()
    fakeSpeech()
    const server = voiceServer()
    server.on(TTS, partReply(2))
    const user = renderApp()
    await toQuestion(server, user, /^Panel voices/)
    await vi.waitFor(() => expect(audio.created).toHaveBeenCalledTimes(2)) // part 0, and part 1 prefetched
    await user.type(screen.getByLabelText('Your answer'), 'An answer.')
    await user.click(screen.getByRole('button', { name: 'Send answer' }))
    await screen.findByText(Q2)
    await vi.waitFor(() => expect(audio.revoked).toEqual(expect.arrayContaining(['blob:clip-1', 'blob:clip-2'])))
  })
})

describe('browser voice', () => {
  it('speaks the question at once in the archetype’s v1 voice, pitch and rate, and never calls /tts', async () => {
    const audio = fakeAudio()
    const speech = fakeSpeech()
    const server = voiceServer()
    const user = renderApp()
    await toQuestion(server, user, /^Browser voice/)
    await vi.waitFor(() => expect(speech.spoken().length).toBeGreaterThan(0))
    const utterance = speech.spoken().at(-1)
    // methodology_expert in v1's VOICE_PROFILES: Aria (Edge only) is missing, so Zira wins.
    expect(utterance).toMatchObject({ text: Q1, pitch: 0.95, rate: 0.94, voice: { name: 'Microsoft Zira - English (United States)' } })
    expect(server.count(TTS)).toBe(0)
    expect(audio.plays).toEqual([])

    act(() => utterance?.onstart?.())
    expect(screen.getByText('Speaking')).toBeInTheDocument()
    act(() => utterance?.onend?.())
    expect(screen.queryByText('Speaking')).not.toBeInTheDocument()
  })

  it('shows a play button when the browser refuses to speak without a click (E10)', async () => {
    fakeAudio()
    const speech = fakeSpeech()
    const server = voiceServer()
    const user = renderApp()
    await toQuestion(server, user, /^Browser voice/)
    await vi.waitFor(() => expect(speech.spoken().length).toBeGreaterThan(0))
    act(() => speech.spoken().at(-1)?.onerror?.({ error: 'not-allowed' }))
    const play = screen.getByRole('button', { name: /Play the question/ })
    const before = speech.spoken().length
    await user.click(play)
    await vi.waitFor(() => expect(speech.spoken().length).toBe(before + 1))
  })
})

describe('no voice', () => {
  it('neither requests audio nor speaks, and shows no voice controls', async () => {
    const audio = fakeAudio()
    const speech = fakeSpeech()
    const server = voiceServer()
    const user = renderApp()
    await toQuestion(server, user, /^No voice/)
    await advance(5000)
    expect(server.count(TTS)).toBe(0)
    expect(audio.plays).toEqual([])
    expect(speech.spoken()).toEqual([])
    expect(screen.queryByRole('button', { name: /Replay/ })).not.toBeInTheDocument()
    expect(screen.queryByText('Voice loading')).not.toBeInTheDocument()
  })
})
