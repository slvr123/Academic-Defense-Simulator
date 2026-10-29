// M10 N5: the no-upload paths. The recorded demo replays a static fixture through the
// real screens with no backend call; the sample-paper button uploads the bundled capstone.
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { DEMO_NOTE, DEMO_PATH, DemoReplay, type DemoRecording, RECORDING_AVAILABLE } from './components/DemoReplay'
import { createPlayer } from './voice/player'
import { lastAnswer, nextQuestion, report, startResponse } from './test/fixtures'
import { defaultServer, renderApp } from './test/renderApp'
import { installWide } from './test/setup'
import { SAMPLE_PDF_PATH } from './state/useFlow'

vi.setConfig({ testTimeout: 15_000 })

// A two-question recording in the fixture's shape; the real one is public/demo/anicheck-defense.json.
const recording: DemoRecording = {
  recorded_at: '2026-10-29T00:00:00+00:00',
  document: 'sample-capstone-anicheck.pdf',
  panel_config: { defense_type: 'capstone', other_subtype: null, domain: 'IT', topic: 'T', archetype_keys: ['methodology_expert'], starting_difficulty: 2 },
  session: {
    status: 'ready',
    chunk_count: 20,
    defense: 'not_started',
    ingest: { page_count: 16, citable_passages: 20, navigation_pages: [], reference_list: { start: 16, end: 16 }, literature_review: null, held_back_passages: 0 },
  },
  start: startResponse,
  turns: [
    { answer: 'Recorded answer one.', response: nextQuestion },
    { answer: 'Recorded answer two.', response: lastAnswer },
  ],
  report,
}

function demoServer() {
  const server = defaultServer()
  server.on(`GET ${DEMO_PATH}`, { status: 200, body: recording })
  return server
}

/** The replay on its own: the start screen hides its button until a recording ships. */
function renderReplay(onExit = vi.fn()) {
  const user = userEvent.setup()
  render(<DemoReplay player={createPlayer()} onExit={onExit} />)
  return { user, onExit }
}

describe('Read a recorded defense (N5)', () => {
  it('is offered on the start screen exactly when the recording is shipped (deferred to M10.1)', async () => {
    const shipped = Object.keys(import.meta.glob('../public/demo/anicheck-defense.json')).length === 1
    expect(RECORDING_AVAILABLE).toBe(shipped)
    defaultServer()
    renderApp()
    await screen.findByText(/defenses left today/)
    expect(screen.queryByRole('button', { name: /Read a recorded defense/ }) !== null).toBe(RECORDING_AVAILABLE)
    expect(screen.getByRole('button', { name: /Try it with the sample paper/ })).toBeInTheDocument()
  })

  it('replays the recording through the real screens with Next, labelled, and makes no backend call', async () => {
    installWide() // the card layout
    const server = demoServer()
    const { user, onExit } = renderReplay()

    // Preparing, with the recording's real ingest numbers.
    expect(await screen.findByText('What the panel had to work with in this recording.')).toBeInTheDocument()
    expect(screen.getAllByText('Recorded demo').length).toBeGreaterThan(0)
    expect(screen.getByText(new RegExp(DEMO_NOTE.replace('.', '\\.')))).toBeInTheDocument()
    expect(screen.getByText('16')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // Question 1 with the recorded answer, read-only.
    expect(await screen.findByText('How did you validate your survey instrument?', { selector: '.sr-only' })).toBeInTheDocument()
    expect(screen.getByLabelText('Your answer')).toHaveValue('Recorded answer one.')
    expect(screen.getByLabelText('Your answer')).toHaveAttribute('readonly')
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // Question 2, with question 1's source revealed and the transcript.
    expect(await screen.findByText('Why thirty respondents?', { selector: '.sr-only' })).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Source passage' })).toHaveTextContent('PDF pages 12–13')
    expect(screen.getByRole('button', { name: 'Transcript · 1 earlier turn' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // The debrief, scores and all.
    expect(await screen.findByText('Your debrief')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Overall' })).getByText('70', { exact: false, selector: 'p' })).toHaveTextContent('70/100')
    // The static fixture only (StrictMode's mount effect asks for it twice), never the API.
    expect(new Set(server.calls)).toEqual(new Set([`GET ${DEMO_PATH}`]))

    await user.click(screen.getByRole('button', { name: 'Leave the demo' }))
    expect(onExit).toHaveBeenCalled()
  })

  it('offers only the browser voice or none, never panel voices', async () => {
    demoServer()
    const { user } = renderReplay()
    await user.click(await screen.findByRole('button', { name: 'Next' }))
    await user.click(await screen.findByRole('button', { name: /^Voice: / }))
    expect(screen.queryByRole('radio', { name: 'Panel voice' })).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Browser voice' })).toBeInTheDocument()
  })

  it('opens the sample PDF at the revealed page', async () => {
    installWide() // the card layout
    demoServer()
    const { user } = renderReplay()
    await user.click(await screen.findByRole('button', { name: 'Next' }))
    await user.click(await screen.findByRole('button', { name: 'Next' }))
    await user.click(await screen.findByRole('button', { name: 'Open at page 12 in your PDF' }))
    const panel = screen.getByRole('region', { name: 'Your PDF' })
    expect(within(panel).getByTitle('Your PDF, page 12')).toHaveAttribute('src', '/sample/sample-capstone-anicheck.pdf#page=12')
  })
})

describe('Try it with the sample paper (N5)', () => {
  it('uploads the bundled capstone as a capstone defense on its Recommended preset', async () => {
    const server = defaultServer()
    server.on(`GET ${SAMPLE_PDF_PATH}`, { status: 200, bytes: new TextEncoder().encode('%PDF-1.7 sample') })
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    await user.click(screen.getByRole('button', { name: /Try it with the sample paper/ }))
    await vi.waitFor(() => expect(server.upload.current).not.toBeNull())
    const form = server.upload.current?.form
    const file = form?.get('file')
    expect(file instanceof File && file.name).toBe('sample-capstone-anicheck.pdf')
    expect(JSON.parse(String(form?.get('panel')))).toMatchObject({ defense_type: 'capstone' })
  })

  it('says so when the sample paper cannot be loaded', async () => {
    const server = defaultServer()
    server.on(`GET ${SAMPLE_PDF_PATH}`, { status: 404, body: {} })
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    await user.click(screen.getByRole('button', { name: /Try it with the sample paper/ }))
    expect(await screen.findByText(/The sample paper couldn't be loaded/)).toBeInTheDocument()
    expect(server.upload.sent).toBe(0)
  })
})
