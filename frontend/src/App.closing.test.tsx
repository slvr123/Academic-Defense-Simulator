// M15: on a hand-off the outgoing panelist closes before the next one asks. The UI
// sequence, the skip, reduced motion, the transcript, and the closing line's voice.
import { act, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AnswerResponse } from './api/types'
import realRecording from '../public/demo/anicheck-defense.json'
import { CLOSING_HOLD_MS } from './components/DefenseScreens'
import { DEMO_PATH } from './components/DemoReplay'
import { evaluation, nextQuestion, question, reyes, startResponse } from './test/fixtures'
import type { FakeServer, Reply } from './test/fakeServer'
import { advance, defaultServer, renderApp, startDefense, uploadAccepted } from './test/renderApp'
import { installMatchMedia } from './test/setup'
import { fakeAudio, fakeSpeech, restoreObjectUrls, WAV } from './test/voiceFakes'

// M6 H7: App-level tests drive the whole UI, so a slow machine needs more than vitest's
// 5 s default. No retries: a failing test still fails.
vi.setConfig({ testTimeout: 15_000 })

const Q1 = 'How did you validate your survey instrument?'
const Q2 = 'Why thirty respondents?'
const LINE = 'Alright, I will leave the instrument there.'
const TTS = 'POST /tts'
const CLIP: Reply = { status: 200, bytes: WAV, headers: { 'Content-Type': 'audio/wav' } }
const handOff: AnswerResponse = { ...nextQuestion, closing: { panelist: reyes, line: LINE } }
// A follow-up: the same panelist asks again, and nobody closes.
const followUp: AnswerResponse = { evaluation, next_question: question(2, Q2), done: false, closing: null }

afterEach(restoreObjectUrls)

function server(answer: AnswerResponse = handOff): FakeServer {
  const fake = defaultServer()
  fake.on('GET /sessions/abc', { status: 200, body: { status: 'ready', chunk_count: 8, defense: 'not_started' } })
  fake.on('POST /sessions/abc/start', { status: 200, body: startResponse })
  fake.on('POST /sessions/abc/answers', { status: 200, body: answer })
  fake.on(TTS, CLIP)
  return fake
}

/** The defense on question 1, on a strict clock so the hold can be timed to the millisecond. */
async function onQuestionOne(fake: FakeServer, voice = /^No voice/) {
  const user = renderApp({ clock: 'strict' })
  await uploadAccepted(fake, user)
  await advance(2000)
  await startDefense(user, voice)
  await advance(0)
  expect(screen.getAllByText(Q1).length).toBeGreaterThan(0) // the typed text, and its sr-only copy under reduced motion
  return user
}

async function answer(user: ReturnType<typeof renderApp>) {
  await user.type(screen.getByLabelText('Your answer'), 'Thirty respondents, purposively sampled.')
  await user.click(screen.getByRole('button', { name: 'Submit answer' }))
  await advance(0)
}

const litCard = () => document.querySelector('[data-lit]')

describe('the outgoing panelist closes (M15)', () => {
  it('keeps the outgoing card lit while the closing line is said alone, then the next question follows after the hold', async () => {
    const user = await onQuestionOne(server())
    await answer(user)

    const closing = screen.getByRole('region', { name: 'Dr. Reyes closes' })
    expect(closing).toHaveTextContent(LINE)
    expect(closing).toHaveTextContent('Methodology Expert')
    expect(screen.queryByLabelText('Your answer')).not.toBeInTheDocument() // no answer box
    expect(screen.queryByText(Q2)).not.toBeInTheDocument()
    expect(litCard()).toHaveTextContent('Dr. Reyes')
    expect(litCard()).toHaveTextContent('Closing')

    await advance(CLOSING_HOLD_MS - 1)
    expect(screen.getByRole('region', { name: 'Dr. Reyes closes' })).toBeInTheDocument()
    await advance(1)
    expect(screen.queryByRole('region', { name: 'Dr. Reyes closes' })).not.toBeInTheDocument()
    expect(screen.getByText(Q2)).toBeInTheDocument()
    expect(screen.getByLabelText('Your answer')).toHaveValue('')
    expect(litCard()).toHaveTextContent('Dr. Santos')
    expect(litCard()).toHaveTextContent('Asking')
  })

  it.each([
    ['a click', () => fireEvent.pointerDown(document.body)],
    ['a key', () => fireEvent.keyDown(window, { key: ' ' })],
  ])('goes straight to the question on %s', async (_name, skip) => {
    const user = await onQuestionOne(server())
    await answer(user)
    expect(screen.getByRole('region', { name: 'Dr. Reyes closes' })).toBeInTheDocument()
    act(skip)
    expect(screen.queryByRole('region', { name: 'Dr. Reyes closes' })).not.toBeInTheDocument()
    expect(screen.getByText(Q2)).toBeInTheDocument()
  })

  it('shows no closing line on a follow-up: the same panelist asks again at once', async () => {
    const user = await onQuestionOne(server(followUp))
    await answer(user)
    expect(screen.queryByRole('region', { name: /closes$/ })).not.toBeInTheDocument()
    expect(screen.getByText(Q2)).toBeInTheDocument()
  })

  it('under reduced motion shows the closing line at once, above the question, with no hold', async () => {
    installMatchMedia((query) => query === '(prefers-reduced-motion: reduce)')
    const user = await onQuestionOne(server())
    await answer(user)
    expect(screen.queryByRole('region', { name: 'Dr. Reyes closes' })).not.toBeInTheDocument()
    expect(screen.getAllByText(Q2).length).toBeGreaterThan(0)
    const said = document.querySelector('[data-closing-said]')
    expect(said).toHaveTextContent(`Dr. Reyes: ${LINE}`)
    expect(within(screen.getByRole('region', { name: 'Question 2' })).getByText(`Dr. Reyes: ${LINE}`)).toBe(said)
  })

  it('lists the closing line in the transcript, after the turn it followed and attributed to its panelist', async () => {
    const user = await onQuestionOne(server())
    await answer(user)
    await advance(CLOSING_HOLD_MS)
    await user.click(screen.getByRole('button', { name: /Transcript · 1 earlier turn/ }))
    const drawer = screen.getByRole('dialog', { name: 'Transcript' })
    const entries = within(drawer).getAllByRole('listitem')
    expect(entries).toHaveLength(1)
    expect(entries[0]).toHaveTextContent(Q1)
    expect(within(entries[0]).getByText(`Dr. Reyes closes: ${LINE}`)).toBeInTheDocument()
  })

  it('speaks the closing line in the outgoing voice first, and the next question only once it ends (browser voice)', async () => {
    fakeAudio()
    const speech = fakeSpeech()
    const user = await onQuestionOne(server(), /^Browser voice/)
    await answer(user)
    const said = speech.spoken()
    expect(said.at(-1)?.text).toBe(LINE)
    expect(said.some((u) => u.text === Q2)).toBe(false)

    await advance(CLOSING_HOLD_MS * 3) // a spoken closing line isn't cut off by the timed hold
    expect(screen.getByRole('region', { name: 'Dr. Reyes closes' })).toBeInTheDocument()

    act(() => said.at(-1)?.onend?.())
    expect(screen.getByText(Q2)).toBeInTheDocument()
    expect(speech.spoken().at(-1)?.text).toBe(Q2)
  })

  it('asks /tts for the closing line of the answered turn, plays it, and holds the next question until it ends (panel voice)', async () => {
    const audio = fakeAudio()
    fakeSpeech()
    const fake = server()
    const bodies: unknown[] = []
    fake.on(TTS, (body) => {
      bodies.push(body)
      return CLIP
    })
    const user = await onQuestionOne(fake, /^Panel voices/)
    await answer(user)
    await advance(0)
    expect(bodies).toContainEqual({ session_id: 'abc', turn: 1, part: null, closing: true })
    expect(bodies).toContainEqual({ session_id: 'abc', turn: 2, part: 0, closing: false }) // fetched during the closing
    const playsBefore = audio.plays.length
    expect(screen.getByRole('region', { name: 'Dr. Reyes closes' })).toBeInTheDocument()

    act(() => audio.fire('ended'))
    await advance(0)
    expect(screen.getByText(Q2)).toBeInTheDocument()
    expect(audio.plays.length).toBe(playsBefore + 1) // the question's clip, after the closing line's
  })

  it('replays a recorded closing line with the same pacing as live (Decision 7)', async () => {
    // The shipped recording predates M15; give its first hand-off a closing line.
    const recording = structuredClone(realRecording) as typeof realRecording & { turns: { response: { closing?: unknown } }[] }
    const outgoing = recording.start.question.panelist
    recording.turns[0].response.closing = { panelist: outgoing, line: LINE }
    const fake = defaultServer()
    fake.on(`GET ${DEMO_PATH}`, { status: 200, body: recording })
    const user = renderApp({ clock: 'strict' })
    await advance(0)
    await user.click(screen.getByRole('button', { name: /Read a recorded defense/ }))
    await advance(0)
    await user.click(screen.getByRole('button', { name: 'Next' })) // the preparing screen -> question 1
    await user.click(screen.getByRole('button', { name: 'Next' })) // the recorded answer -> question 2
    await advance(0)
    const name = `Dr. ${outgoing.name} closes`
    expect(screen.getByRole('region', { name })).toHaveTextContent(LINE)
    await advance(CLOSING_HOLD_MS - 1)
    expect(screen.getByRole('region', { name })).toBeInTheDocument()
    await advance(1)
    expect(screen.queryByRole('region', { name })).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Question 2' })).toBeInTheDocument()
  })
})
