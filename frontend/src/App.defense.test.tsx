// D16's defense-loop rows, D17's scores, D18's source reveal and D10's keep-alive,
// through the real App with a scripted network.
import { act, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { FakeServer, Reply } from './test/fakeServer'
import { lastAnswer, nextQuestion, report, startResponse } from './test/fixtures'
import { advance, defaultServer, deferred, renderApp, uploadAccepted } from './test/renderApp'

type User = ReturnType<typeof renderApp>

const Q1 = 'How did you validate your survey instrument?'
const Q2 = 'Why thirty respondents?'
const SOURCE_1 = 'Source passage for turn 1: the instrument was adapted from ISO/IEC 25010.'
const SOURCE_2 = 'Source passage for turn 2: the instrument was adapted from ISO/IEC 25010.'
const ANSWERS = 'POST /sessions/abc/answers'
const START = 'POST /sessions/abc/start'

function defenseServer(): FakeServer {
  const server = defaultServer()
  server.on('GET /sessions/abc', { status: 200, body: { status: 'ready', chunk_count: 8, defense: 'not_started' } })
  server.on(START, { status: 200, body: startResponse })
  server.on(ANSWERS, { status: 200, body: nextQuestion })
  server.on('GET /sessions/abc/report', { status: 200, body: report })
  return server
}

async function toReady(server: FakeServer, user: User) {
  await uploadAccepted(server, user)
  await advance(2000)
  await screen.findByText('Your document is ready')
}

async function toQuestion(server: FakeServer, user: User) {
  await toReady(server, user)
  await user.click(screen.getByRole('button', { name: 'Start the defense' }))
  await screen.findByText(Q1)
}

async function answer(user: User, text = 'We adapted ISO 25010.') {
  await user.type(screen.getByLabelText('Your answer'), text)
  await user.click(screen.getByRole('button', { name: 'Send answer' }))
}

describe('preparing (D16, /start in flight)', () => {
  it('shows the seated panel and staged copy, never a bare spinner', async () => {
    const server = defenseServer()
    const start = deferred<Reply>()
    server.on(START, () => start.promise)
    const user = renderApp()
    await toReady(server, user)
    await user.click(screen.getByRole('button', { name: 'Start the defense' }))

    expect(screen.getByText('The panel is preparing. They are reading your document.')).toBeInTheDocument()
    const seats = screen.getByRole('list', { name: 'Seated panel' })
    expect(within(seats).getByText('Methodology Expert')).toBeInTheDocument()
    expect(within(seats).getByText("Devil's Advocate")).toBeInTheDocument()

    await advance(8000)
    expect(screen.getByText('The panel is preparing. Each panelist is choosing what to press you on.')).toBeInTheDocument()

    await act(async () => start.resolve({ status: 200, body: startResponse }))
    expect(await screen.findByText(Q1)).toBeInTheDocument()
    expect(screen.getAllByText('Dr. Reyes').length).toBeGreaterThan(0)
    expect(server.count(START)).toBe(1) // StrictMode didn't send it twice
  })

  it.each([
    ['per_ip_limit', "You've used your 3 defenses for today."],
    ['daily_cap', "Today's defenses are all used."],
  ])('shows a %s 429 at start with a way to start over', async (code, text) => {
    const server = defenseServer()
    server.on(START, { status: 429, body: { detail: 'x', code }, headers: { 'Retry-After': '3600' } })
    const user = renderApp()
    await toReady(server, user)
    await user.click(screen.getByRole('button', { name: 'Start the defense' }))
    expect(await screen.findByText(new RegExp(`^${text.replace(/[.']/g, '.')} More at .+, Manila time\\.$`))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start over' })).toBeInTheDocument()
  })

  it('counts down a busy 503 on start and retries it once by itself', async () => {
    const server = defenseServer()
    server.on(START, { status: 503, body: { detail: 'The panel is busy right now.' }, headers: { 'Retry-After': '60' } })
    const user = renderApp()
    await toReady(server, user)
    await user.click(screen.getByRole('button', { name: 'Start the defense' }))
    expect(await screen.findByText('The panel is busy - retrying in 60 s.')).toBeInTheDocument()
    server.on(START, { status: 200, body: startResponse })
    await advance(60_000)
    expect(await screen.findByText(Q1)).toBeInTheDocument()
    expect(server.count(START)).toBe(2)
  })
})

describe('the answer loop', () => {
  it('holds the open question’s source back, then reveals it with the 100-point scores after the answer (D17, D18)', async () => {
    const server = defenseServer()
    const user = renderApp()
    await toQuestion(server, user)
    expect(screen.queryByText(SOURCE_1)).not.toBeInTheDocument()

    await answer(user)
    expect(await screen.findByText(Q2)).toBeInTheDocument()
    expect(screen.getByText(SOURCE_1)).toBeInTheDocument()
    expect(screen.getByText('Pages 12-13')).toBeInTheDocument()
    expect(screen.queryByText(SOURCE_2)).not.toBeInTheDocument()

    // clarity 4, depth 3, grounding 4 -> 80, 60, 80; overall 11 / 3 -> 73
    const scores = screen.getByText(/How the panel scored your answer to question 1/).closest('section')
    if (scores === null) throw new Error('no score card')
    expect(within(scores).getAllByText('80/100')).toHaveLength(2)
    expect(within(scores).getByText('60/100')).toBeInTheDocument()
    expect(within(scores).getByText('73/100')).toBeInTheDocument()
    expect(within(scores).getByText(/The scale floor is 20\./)).toBeInTheDocument()
    expect(screen.getByLabelText('Your answer')).toHaveValue('')
  })

  it('disables sending from the click until the response, and waits out a 409 by resending (E13)', async () => {
    const server = defenseServer()
    const reply = deferred<Reply>()
    server.on(ANSWERS, () => reply.promise)
    const user = renderApp()
    await toQuestion(server, user)
    await answer(user)

    expect(screen.getByText('The panel is weighing your answer.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Send answer' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Your answer')).toHaveAttribute('readonly')

    server.on(ANSWERS, { status: 200, body: nextQuestion })
    await act(async () => reply.resolve({ status: 409, body: { detail: 'The panel is still working on this session.' } }))
    expect(screen.getByText('The panel is weighing your answer.')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(server.count(ANSWERS)).toBe(1)
    await advance(3000)
    expect(await screen.findByText(Q2)).toBeInTheDocument()
    expect(server.count(ANSWERS)).toBe(2)
  })

  it('gives up waiting after 10 resends of a 409 and offers Try again (E13)', async () => {
    const server = defenseServer()
    server.on(ANSWERS, { status: 409, body: { detail: 'The panel is still working on this session.' } })
    const user = renderApp()
    await toQuestion(server, user)
    await answer(user, 'My answer')
    for (let resend = 1; resend <= 10; resend++) await advance(3000)
    expect(await screen.findByText('The panel is still working on your answer.')).toBeInTheDocument()
    expect(server.count(ANSWERS)).toBe(11) // the first send and 10 resends
    expect(screen.getByLabelText('Your answer')).toHaveValue('My answer')
    await advance(60_000)
    expect(server.count(ANSWERS)).toBe(11)

    server.on(ANSWERS, { status: 200, body: nextQuestion })
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(Q2)).toBeInTheDocument()
  })

  it('recovers a lost response: Try again resends the same turn and the server replays it (E13)', async () => {
    const server = defenseServer()
    const sent: unknown[] = []
    server.on(ANSWERS, (body) => {
      sent.push(body)
      return 'network' // the server recorded the turn, but the response never arrived
    })
    const user = renderApp()
    await toQuestion(server, user)
    await answer(user, 'My answer')
    expect(await screen.findByText("Can't reach the server.")).toBeInTheDocument()
    server.on(ANSWERS, (body) => {
      sent.push(body)
      return { status: 200, body: nextQuestion } // the stored response, replayed
    })
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(Q2)).toBeInTheDocument()
    expect(sent).toEqual([
      { text: 'My answer', turn: 1 },
      { text: 'My answer', turn: 1 },
    ])
  })

  it('shows a stale_question 409 as the server words it (E13)', async () => {
    const server = defenseServer()
    const text = "That answer is for a question that isn't open any more. The defense has moved on without it."
    server.on(ANSWERS, { status: 409, body: { detail: text, code: 'stale_question' } })
    const user = renderApp()
    await toQuestion(server, user)
    await answer(user)
    expect(await screen.findByText(text)).toBeInTheDocument()
    await advance(30_000)
    expect(server.count(ANSWERS)).toBe(1) // never resent
  })

  it('keeps the answer after a 502 and resends the same text (D16)', async () => {
    const server = defenseServer()
    const sent: unknown[] = []
    server.on(ANSWERS, (body) => {
      sent.push(body)
      return { status: 502, body: { detail: 'The panel hit a problem with that turn.' } }
    })
    const user = renderApp()
    await toQuestion(server, user)
    await answer(user, 'My first answer')

    expect(await screen.findByText("That turn didn't go through - nothing was recorded. Send it again.")).toBeInTheDocument()
    expect(screen.getByLabelText('Your answer')).toHaveValue('My first answer')
    server.on(ANSWERS, (body) => {
      sent.push(body)
      return { status: 200, body: nextQuestion }
    })
    await user.click(screen.getByRole('button', { name: 'Send it again' }))
    expect(await screen.findByText(Q2)).toBeInTheDocument()
    expect(sent).toEqual([{ text: 'My first answer', turn: 1 }, { text: 'My first answer', turn: 1 }])
  })

  it('counts down a busy 503, retries once by itself, then offers Try again only (D16)', async () => {
    const server = defenseServer()
    server.on(ANSWERS, { status: 503, body: { detail: 'The panel is busy right now. Please try again in a minute.' }, headers: { 'Retry-After': '60' } })
    const user = renderApp()
    await toQuestion(server, user)
    await answer(user)

    expect(await screen.findByText('The panel is busy - retrying in 60 s.')).toBeInTheDocument()
    await advance(30_000)
    expect(screen.getByText('The panel is busy - retrying in 30 s.')).toBeInTheDocument()
    await advance(30_000)
    expect(await screen.findByText('The panel is busy right now. Please try again in a minute.')).toBeInTheDocument()
    expect(server.count(ANSWERS)).toBe(2)

    await advance(180_000)
    expect(server.count(ANSWERS)).toBe(2) // no second automatic retry
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('says it can’t reach the server, and Try again resends', async () => {
    const server = defenseServer()
    server.on(ANSWERS, 'network')
    const user = renderApp()
    await toQuestion(server, user)
    await answer(user)
    expect(await screen.findByText("Can't reach the server.")).toBeInTheDocument()
    server.on(ANSWERS, { status: 200, body: nextQuestion })
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(Q2)).toBeInTheDocument()
  })

  it('sends a 404 mid-defense to the expired screen', async () => {
    const server = defenseServer()
    server.on(ANSWERS, { status: 404, body: { detail: 'Session not found.' } })
    const user = renderApp()
    await toQuestion(server, user)
    await answer(user)
    expect(await screen.findByText('This session expired - upload your document again.')).toBeInTheDocument()
  })

  it('fetches the report after the last answer', async () => {
    const server = defenseServer()
    server.on(ANSWERS, { status: 200, body: lastAnswer })
    const user = renderApp()
    await toQuestion(server, user)
    await answer(user)
    await vi.waitFor(() => expect(server.count('GET /sessions/abc/report')).toBe(1))
  })
})

describe('keep-alive (D10)', () => {
  const healthCalls = (server: FakeServer) => server.count('GET /status')

  it('pings /status every 10 minutes while a question is open, and not before', async () => {
    const server = defenseServer()
    const user = renderApp()
    await toReady(server, user)
    const atLoad = healthCalls(server)
    await advance(25 * 60_000)
    expect(healthCalls(server)).toBe(atLoad) // ready: no pings

    await user.click(screen.getByRole('button', { name: 'Start the defense' }))
    await screen.findByText(Q1)
    await advance(10 * 60_000)
    expect(healthCalls(server)).toBe(atLoad + 1)
    await advance(10 * 60_000)
    expect(healthCalls(server)).toBe(atLoad + 2)
  })

  it('keeps pinging while an answer is being submitted, and stops at the report', async () => {
    const server = defenseServer()
    const reply = deferred<Reply>()
    server.on(ANSWERS, () => reply.promise)
    const user = renderApp()
    await toQuestion(server, user)
    const before = healthCalls(server)
    await answer(user)
    await advance(10 * 60_000)
    expect(healthCalls(server)).toBe(before + 1)

    await act(async () => reply.resolve({ status: 200, body: lastAnswer }))
    await vi.waitFor(() => expect(server.count('GET /sessions/abc/report')).toBe(1))
    const atReport = healthCalls(server)
    await advance(30 * 60_000)
    expect(healthCalls(server)).toBe(atReport)
  })
})
