// M10's session comforts through the real App: End session (N2), the voice menu (N9),
// the answer box (N8), the transcript drawer (N10) and the candidate's own PDF (N4).
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { QuestionScreen } from './components/DefenseScreens'
import type { FakeServer } from './test/fakeServer'
import { evaluation, nextQuestion, report, startResponse } from './test/fixtures'
import { advance, defaultServer, renderApp, startDefense, uploadAccepted } from './test/renderApp'
import { fakeSpeech } from './test/voiceFakes'
import { createPlayer } from './voice/player'

vi.setConfig({ testTimeout: 15_000 })

type User = ReturnType<typeof renderApp>

const Q1 = 'How did you validate your survey instrument?'
const Q2 = 'Why thirty respondents?'
const ANSWERS = 'POST /sessions/abc/answers'
const DRAFT_KEY = 'ads.answer-draft:abc:1'

function sessionServer(): FakeServer {
  const server = defaultServer()
  server.on('GET /sessions/abc', { status: 200, body: { status: 'ready', chunk_count: 8, defense: 'not_started' } })
  server.on('POST /sessions/abc/start', { status: 200, body: startResponse })
  server.on(ANSWERS, { status: 200, body: nextQuestion })
  server.on('GET /sessions/abc/report', { status: 200, body: report })
  server.on('DELETE /sessions/abc', { status: 204 })
  return server
}

async function toQuestion(server: FakeServer, user: User) {
  await uploadAccepted(server, user)
  await advance(2000)
  await screen.findByText('Your document is ready')
  await startDefense(user)
  await screen.findByText(Q1)
}

async function answerFirst(user: User, text = 'We adapted ISO 25010.') {
  await user.type(screen.getByLabelText('Your answer'), text)
  await user.click(screen.getByRole('button', { name: 'Submit answer' }))
  await screen.findByText(Q2)
}

describe('the session header (N2)', () => {
  it('shows the title and type, the question with its progress dots, and no invented total', async () => {
    const server = sessionServer()
    const user = renderApp()
    await toQuestion(server, user)
    const header = screen.getByRole('banner')
    expect(header).toHaveTextContent('Library Management System · Thesis defense')
    expect(header).toHaveTextContent('Question 1')
    expect(header).not.toHaveTextContent(/Round|of \d/)
  })

  it('ends the session only after an in-app confirm, deletes it, and returns to the start screen', async () => {
    const confirm = vi.fn(() => true)
    vi.stubGlobal('confirm', confirm)
    const server = sessionServer()
    const user = renderApp()
    await toQuestion(server, user)

    await user.click(screen.getByRole('button', { name: 'End session' }))
    const dialog = screen.getByRole('alertdialog', { name: 'End session?' })
    await user.click(within(dialog).getByRole('button', { name: 'Keep going' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(server.count('DELETE /sessions/abc')).toBe(0)

    await user.click(screen.getByRole('button', { name: 'End session' }))
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'End session' }))
    expect(await screen.findByText(/Drop your PDF here/)).toBeInTheDocument()
    expect(server.count('DELETE /sessions/abc')).toBe(1)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('still ends the session here when the DELETE fails', async () => {
    const server = sessionServer()
    server.on('DELETE /sessions/abc', 'network')
    const user = renderApp()
    await toQuestion(server, user)
    await user.click(screen.getByRole('button', { name: 'End session' }))
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'End session' }))
    expect(await screen.findByText(/Drop your PDF here/)).toBeInTheDocument()
  })
})

describe('the voice menu (N9)', () => {
  it('switches mid-session, showing the panel-voice disclosure the first time only', async () => {
    fakeSpeech()
    const server = sessionServer()
    const tts = vi.fn(() => ({ status: 503 as const, body: { detail: 'off', code: 'tts_unavailable' } }))
    server.on('POST /tts', tts)
    const user = renderApp()
    await toQuestion(server, user)

    const menu = () => screen.getByRole('button', { name: /^Voice: / })
    expect(menu()).toHaveAccessibleName('Voice: No voice. Change voice')
    await user.click(menu())
    await user.click(screen.getByRole('radio', { name: 'Panel voice' }))
    expect(screen.getByText(/to Xiaomi's Mimo API/)).toBeInTheDocument()
    expect(tts).not.toHaveBeenCalled() // nothing goes to MiMo before the confirm
    await user.click(screen.getByRole('button', { name: 'Use panel voices' }))
    expect(menu()).toHaveAccessibleName('Voice: Panel voice. Change voice')
    await vi.waitFor(() => expect(tts).toHaveBeenCalled())

    await user.click(menu())
    await user.click(screen.getByRole('radio', { name: 'Browser voice' }))
    expect(menu()).toHaveAccessibleName('Voice: Browser voice. Change voice')
    await user.click(menu())
    await user.click(screen.getByRole('radio', { name: 'Panel voice' }))
    expect(screen.queryByText(/to Xiaomi's Mimo API/)).not.toBeInTheDocument() // shown once per session
    expect(menu()).toHaveAccessibleName('Voice: Panel voice. Change voice')
  })

  it('offers no panel voice when the server has none, and closes on Esc', async () => {
    const server = sessionServer()
    server.on('GET /status', { status: 200, body: { status: 'ok', model_loaded: true, tts_available: false } })
    const user = renderApp()
    await toQuestion(server, user)
    await user.click(screen.getByRole('button', { name: /^Voice: / }))
    expect(screen.queryByRole('radio', { name: 'Panel voice' })).not.toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('radio', { name: 'Browser voice' })).not.toBeInTheDocument()
  })
})

describe('the answer box (N8)', () => {
  it('counts words and sends on Ctrl+Enter or Cmd+Enter', async () => {
    const server = sessionServer()
    const user = renderApp()
    await toQuestion(server, user)
    const box = screen.getByLabelText('Your answer')
    await user.type(box, 'We adapted ISO 25010.')
    expect(screen.getByText(/^4 words/)).toBeInTheDocument()
    await user.keyboard('{Control>}{Enter}{/Control}')
    expect(await screen.findByText(Q2)).toBeInTheDocument()
    expect(server.count(ANSWERS)).toBe(1)

    await user.type(screen.getByLabelText('Your answer'), 'Power analysis.')
    await user.keyboard('{Meta>}{Enter}{/Meta}')
    await vi.waitFor(() => expect(server.count(ANSWERS)).toBe(2))
  })

  it('saves the draft per session and turn, brings it back, and clears it once sent', async () => {
    localStorage.setItem(DRAFT_KEY, 'Half-written before a reload')
    const server = sessionServer()
    const user = renderApp()
    await toQuestion(server, user)
    const box = screen.getByLabelText('Your answer')
    expect(box).toHaveValue('Half-written before a reload')
    await user.type(box, ', now finished.')
    expect(localStorage.getItem(DRAFT_KEY)).toBe('Half-written before a reload, now finished.')
    await user.click(screen.getByRole('button', { name: 'Submit answer' }))
    await screen.findByText(Q2)
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull()
  })

  it('keeps the draft when the send fails', async () => {
    const server = sessionServer()
    server.on(ANSWERS, { status: 502, body: { detail: 'model error' } })
    const user = renderApp()
    await toQuestion(server, user)
    await user.type(screen.getByLabelText('Your answer'), 'Kept')
    await user.click(screen.getByRole('button', { name: 'Submit answer' }))
    await screen.findByText(/That turn didn't go through/)
    expect(localStorage.getItem(DRAFT_KEY)).toBe('Kept')
  })

  it('works as before when storage throws', async () => {
    const thrower = () => {
      throw new DOMException('denied', 'SecurityError')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(thrower)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(thrower)
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(thrower)
    try {
      const server = sessionServer()
      const user = renderApp()
      await toQuestion(server, user)
      await user.type(screen.getByLabelText('Your answer'), 'Still typing')
      expect(screen.getByLabelText('Your answer')).toHaveValue('Still typing')
      await user.click(screen.getByRole('button', { name: 'Submit answer' }))
      expect(await screen.findByText(Q2)).toBeInTheDocument()
    } finally {
      vi.restoreAllMocks()
    }
  })
})

describe('the transcript drawer (N10)', () => {
  it('lists earlier turns with pages and no scores, traps focus, and closes on Esc', async () => {
    const server = sessionServer()
    const user = renderApp()
    await toQuestion(server, user)
    expect(screen.queryByRole('button', { name: /Transcript/ })).not.toBeInTheDocument() // nothing earlier yet
    await answerFirst(user)

    const opener = screen.getByRole('button', { name: 'Transcript · 1 earlier turn' })
    await user.click(opener)
    const drawer = screen.getByRole('dialog', { name: 'Transcript' })
    expect(drawer).toHaveTextContent(Q1)
    expect(drawer).toHaveTextContent('We adapted ISO 25010.')
    expect(drawer).toHaveTextContent('PDF pages 12–13')
    // Sealed until the debrief (M8 K7): no score, total or gap.
    expect(drawer).not.toHaveTextContent(/\/100|Clarity|Depth|Grounding|No reliability coefficient/)

    const close = within(drawer).getByRole('button', { name: 'Close transcript' })
    expect(close).toHaveFocus()
    await user.tab()
    expect(close).toHaveFocus() // the only control, so Tab stays on it
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })
})

describe("the candidate's own PDF (N4)", () => {
  it('opens at the revealed page from the source card and the header, and revokes the URL when the session ends', async () => {
    const revoke = vi.spyOn(URL, 'revokeObjectURL')
    const server = sessionServer()
    const user = renderApp()
    await toQuestion(server, user)

    // Before any reveal, "Your document" opens page 1.
    await user.click(screen.getByRole('button', { name: 'Your document' }))
    expect(screen.getByTitle('Your PDF, page 1').getAttribute('src')).toMatch(/^blob:.+#page=1$/)
    await user.click(screen.getByRole('button', { name: 'Close PDF' }))
    expect(screen.queryByRole('region', { name: 'Your PDF' })).not.toBeInTheDocument()

    await answerFirst(user)
    await user.click(screen.getByRole('button', { name: 'Open at page 12 in your PDF' }))
    const frame = screen.getByTitle('Your PDF, page 12')
    const url = frame.getAttribute('src')?.replace('#page=12', '')
    expect(screen.getByRole('region', { name: 'Your PDF' })).toHaveTextContent('Nothing is uploaded again.')
    expect(server.count('POST /sessions')).toBe(1) // the one upload, no other

    await user.click(screen.getByRole('button', { name: 'End session' }))
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'End session' }))
    await screen.findByText(/Drop your PDF here/)
    expect(revoke).toHaveBeenCalledWith(url)
    revoke.mockRestore()
  })

  it('hides the open-at-page button when the file is gone', () => {
    const turn = { question: startResponse.question, answer: 'A', evaluation }
    render(
      <QuestionScreen
        sessionId="abc"
        voiceChoice="none"
        player={createPlayer()}
        panel={startResponse.panel}
        question={nextQuestion.next_question ?? startResponse.question}
        draft=""
        last={turn}
        turns={[turn]}
        submitting={false}
        notice={null}
        onChange={() => {}}
        onSend={() => {}}
        onAutoRetry={() => {}}
        onOpenPage={null}
        documentPanel={null}
      />,
    )
    expect(screen.getByRole('complementary', { name: 'Source passage' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Open at page/ })).not.toBeInTheDocument()
  })
})

describe('the paged reveal (M10 follow-up)', () => {
  const reveal = () => screen.getByRole('complementary', { name: 'Source passage' })
  const counter = () => within(reveal()).getByText(/^\d+ \/ \d+$/)

  it('pages between the passage and the question with your answer, by button, arrow key and swipe', async () => {
    const server = sessionServer()
    const user = renderApp()
    await toQuestion(server, user)
    await answerFirst(user)

    expect(counter()).toHaveTextContent('1 / 2')
    const page = (name: string) => screen.getByRole('group', { name })
    expect(within(page('1 of 2')).getByText('adapted from ISO/IEC 25010', { selector: 'mark' })).toBeInTheDocument()
    expect(within(page('1 of 2')).getByRole('button', { name: 'Open at page 12 in your PDF' })).toBeInTheDocument()
    expect(page('2 of 2')).toHaveAttribute('inert') // off-page content can't take focus

    await user.click(within(reveal()).getByRole('button', { name: 'Next page' }))
    expect(counter()).toHaveTextContent('2 / 2')
    expect(page('2 of 2')).toHaveTextContent(Q1)
    expect(page('2 of 2')).toHaveTextContent('We adapted ISO 25010.')
    expect(within(reveal()).getByRole('button', { name: 'Next page' })).toBeDisabled()

    reveal().focus()
    await user.keyboard('{ArrowLeft}')
    expect(counter()).toHaveTextContent('1 / 2')
    await user.keyboard('{ArrowRight}')
    expect(counter()).toHaveTextContent('2 / 2')

    const body = page('1 of 2').parentElement?.parentElement
    if (!body) throw new Error('no page body')
    fireEvent.pointerDown(body, { pointerType: 'touch', clientX: 100 })
    fireEvent.pointerUp(body, { pointerType: 'touch', clientX: 200 }) // swipe right: back
    expect(counter()).toHaveTextContent('1 / 2')
    fireEvent.pointerDown(body, { pointerType: 'touch', clientX: 200 })
    fireEvent.pointerUp(body, { pointerType: 'touch', clientX: 180 }) // too short to count
    expect(counter()).toHaveTextContent('1 / 2')
  })

  it('starts each new reveal on page 1', async () => {
    const server = sessionServer()
    const user = renderApp()
    await toQuestion(server, user)
    await answerFirst(user)
    await user.click(within(reveal()).getByRole('button', { name: 'Next page' }))
    expect(counter()).toHaveTextContent('2 / 2')

    server.on(ANSWERS, { status: 200, body: { evaluation, next_question: { ...nextQuestion.next_question, turn: 3, question: 'Third?' }, done: false } })
    await user.type(screen.getByLabelText('Your answer'), 'Power analysis.')
    await user.click(screen.getByRole('button', { name: 'Submit answer' }))
    await screen.findByText('Third?', { selector: '.sr-only' })
    expect(within(reveal()).getByText('Question 2')).toBeInTheDocument()
    expect(counter()).toHaveTextContent('1 / 2')
  })

  it('shares one slot with the PDF: opening it swaps the reveal out, Back to passage swaps it back', async () => {
    const server = sessionServer()
    const user = renderApp()
    await toQuestion(server, user)
    await answerFirst(user)
    await user.click(screen.getByRole('button', { name: 'Open at page 12 in your PDF' }))
    expect(screen.getByRole('region', { name: 'Your PDF' })).toBeInTheDocument()
    expect(screen.queryByRole('complementary', { name: 'Source passage' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Back to passage/ }))
    expect(screen.queryByRole('region', { name: 'Your PDF' })).not.toBeInTheDocument()
    expect(reveal()).toBeInTheDocument()
  })
})
