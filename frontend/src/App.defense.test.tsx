// D16's defense-loop rows, D17's scores, D18's source reveal and D10's keep-alive,
// through the real App with a scripted network.
import { act, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { FakeServer, Reply } from './test/fakeServer'
import { lastAnswer, nextQuestion, report, startResponse } from './test/fixtures'
import { advance, defaultServer, deferred, renderApp, startDefense, uploadAccepted } from './test/renderApp'

// M6 H7: App-level tests drive the whole UI with real-time polling, so a slow machine
// needs more than vitest's 5 s default. No retries: a failing test still fails.
vi.setConfig({ testTimeout: 15_000 })

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
  await startDefense(user)
  await screen.findByText(Q1)
}

async function answer(user: User, text = 'We adapted ISO 25010.') {
  await user.type(screen.getByLabelText('Your answer'), text)
  await user.click(screen.getByRole('button', { name: 'Submit answer' }))
}

describe('preparing (D16, /start in flight)', () => {
  it('shows what the panel has to work with and what each seat looks for, never a bare spinner (M10 N6)', async () => {
    const server = defenseServer()
    server.on('GET /sessions/abc', {
      status: 200,
      body: {
        status: 'ready',
        chunk_count: 8,
        defense: 'not_started',
        ingest: {
          page_count: 40,
          citable_passages: 6,
          navigation_pages: [{ start: 3, end: 4 }],
          reference_list: { start: 38, end: 40 },
          literature_review: { start: 9, end: 15 },
          held_back_passages: 2,
        },
      },
    })
    const start = deferred<Reply>()
    server.on(START, () => start.promise)
    const user = renderApp()
    await toReady(server, user)
    await startDefense(user)

    expect(screen.getByRole('heading', { name: 'The panel is reading your paper' })).toBeInTheDocument()
    expect(screen.getByText(/Usually 30–60 seconds/)).toBeInTheDocument() // measured (M7 J6)
    // Real numbers from the session status, never invented ones.
    expect(screen.getByText('40')).toBeInTheDocument()
    expect(screen.getByText('pages in your PDF')).toBeInTheDocument()
    expect(screen.getByText('6')).toBeInTheDocument()
    expect(screen.getByText('Contents and list pages · pp. 3–4')).toBeInTheDocument()
    expect(screen.getByText('Reference list · pp. 38–40')).toBeInTheDocument()
    expect(screen.getByText('Literature review · pp. 9–15 · 2 passages held back in this version')).toBeInTheDocument()

    const seats = within(screen.getByRole('list', { name: 'Panel preparation' })).getAllByRole('listitem')
    expect(seats).toHaveLength(4) // the Recommended thesis preset, then the Devil's Advocate
    expect(seats[0]).toHaveTextContent('Methodology Expert')
    expect(seats[0]).toHaveTextContent('Writing question 1')
    expect(seats[0]).toHaveTextContent('whether your approach answers your question')
    expect(seats[1]).toHaveTextContent('Seated')
    expect(seats[3]).toHaveTextContent("Devil's Advocate")
    expect(seats[3]).toHaveTextContent('Speaks last')
    expect(document.body).not.toHaveTextContent(/Reading Chapter/)

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
    server.on(START, { status: 429, body: { detail: 'x', code, limit: 3 }, headers: { 'Retry-After': '3600' } })
    const user = renderApp()
    await toReady(server, user)
    await startDefense(user)
    expect(await screen.findByText(new RegExp(`^${text.replace(/[.']/g, '.')} More at .+, Manila time\\.$`))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start over' })).toBeInTheDocument()
  })

  it("uses the per-IP limit the 429 carries, not a fixed 3 (M6 H6)", async () => {
    const server = defenseServer()
    server.on(START, { status: 429, body: { detail: 'x', code: 'per_ip_limit', limit: 5 }, headers: { 'Retry-After': '3600' } })
    const user = renderApp()
    await toReady(server, user)
    await startDefense(user)
    expect(await screen.findByText(/^You've used your 5 defenses for today\./)).toBeInTheDocument()
  })

  it('resends a /start that met a 409 and opens the replayed first question (M6 H4)', async () => {
    const server = defenseServer()
    let starts = 0
    server.on(START, () => (++starts === 1 ? { status: 409, body: { detail: 'still running' } } : { status: 200, body: startResponse }))
    const user = renderApp()
    await toReady(server, user)
    await startDefense(user)
    await advance(3000)
    expect(await screen.findByText(Q1)).toBeInTheDocument()
    expect(server.count(START)).toBe(2)
  })

  it('shows already_started as the server words it, and never resends it (M6 H4)', async () => {
    const server = defenseServer()
    const text = 'This defense has already started. Answer the current question instead.'
    server.on(START, { status: 409, body: { detail: text, code: 'already_started' } })
    const user = renderApp()
    await toReady(server, user)
    await startDefense(user)
    expect(await screen.findByText(text)).toBeInTheDocument()
    await advance(30_000)
    expect(server.count(START)).toBe(1)
  })

  it('counts down a busy 503 on start and retries it once by itself', async () => {
    const server = defenseServer()
    server.on(START, { status: 503, body: { detail: 'The panel is busy right now.' }, headers: { 'Retry-After': '60' } })
    const user = renderApp()
    await toReady(server, user)
    await startDefense(user)
    expect(await screen.findByText('The panel is busy - retrying in 60 s.')).toBeInTheDocument()
    server.on(START, { status: 200, body: startResponse })
    await advance(60_000)
    expect(await screen.findByText(Q1)).toBeInTheDocument()
    expect(server.count(START)).toBe(2)
  })
})

describe('the answer loop', () => {
  it('holds the open question’s source back, then reveals it with its highlight and no scores after the answer (D18, M8 K6, K7)', async () => {
    const server = defenseServer()
    const user = renderApp()
    await toQuestion(server, user)
    expect(document.body).not.toHaveTextContent(SOURCE_1) // the passage is split around its highlight, so match on text content

    await answer(user)
    expect(await screen.findByText(Q2)).toBeInTheDocument()
    expect(document.body).toHaveTextContent(SOURCE_1)
    expect(screen.getByText('PDF pages 12–13')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(SOURCE_2)

    const reveal = screen.getByRole('complementary', { name: 'Source passage' })
    expect(within(reveal).getByText('What Dr. Reyes was reading')).toBeInTheDocument()
    expect(within(reveal).getByText('adapted from ISO/IEC 25010', { selector: 'mark' })).toBeInTheDocument()
    expect(within(reveal).getByText('Answer recorded. Scores stay sealed until the debrief.')).toBeInTheDocument()
    // K7: the evaluation came back (80, 60, 80; 73 overall) but no score is on screen.
    expect(screen.queryByText(/\/100/)).not.toBeInTheDocument()
    expect(screen.queryByText(/scale floor/)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Your answer')).toHaveValue('')
  })

  it('disables sending from the click until the response, and waits out a 409 by resending (E13)', async () => {
    const server = defenseServer()
    const reply = deferred<Reply>()
    server.on(ANSWERS, () => reply.promise)
    const user = renderApp()
    await toQuestion(server, user)
    await answer(user)

    expect(screen.getByText('Dr. Reyes is reviewing your answer.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Submit answer' })).not.toBeInTheDocument()
    // The answer box gives way to the answer as sent while the panel reviews it.
    expect(screen.queryByLabelText('Your answer')).not.toBeInTheDocument()
    expect(screen.getByText('We adapted ISO 25010.')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Turn progress' })).toHaveTextContent('Reviewing')

    server.on(ANSWERS, { status: 200, body: nextQuestion })
    await act(async () => reply.resolve({ status: 409, body: { detail: 'The panel is still working on this session.' } }))
    expect(screen.getByText('Dr. Reyes is reviewing your answer.')).toBeInTheDocument()
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

  it('after a stale_question 409, moves to the open question and keeps the typed answer (M6 H5)', async () => {
    const server = defenseServer()
    server.on(ANSWERS, { status: 409, body: { detail: 'moved on', code: 'stale_question' } })
    server.on('GET /sessions/abc/current', { status: 200, body: { done: false, question: nextQuestion.next_question } })
    const user = renderApp()
    await toQuestion(server, user)
    await answer(user, 'My typed answer')
    expect(await screen.findByText(Q2)).toBeInTheDocument()
    expect(screen.getByText("This answer was for an earlier question. You're now on question 2.")).toBeInTheDocument()
    expect(screen.getByLabelText('Your answer')).toHaveValue('My typed answer')
    await advance(30_000)
    expect(server.count(ANSWERS)).toBe(1) // never resent
  })

  it("shows the stale_question 409 as the server words it when /current can't answer (E13)", async () => {
    const server = defenseServer()
    const text = "That answer is for a question that isn't open any more. The defense has moved on without it."
    server.on(ANSWERS, { status: 409, body: { detail: text, code: 'stale_question' } })
    server.on('GET /sessions/abc/current', 'network')
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

    await startDefense(user)
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
