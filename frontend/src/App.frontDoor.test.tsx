// D16's front-door rows, rendered through the real App with a scripted network.
import { act, fireEvent, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { pdf, quota } from './test/fixtures'
import { advance, chooseFile, defaultServer, deferred, HEALTHY, renderApp, submitDocument, uploadAccepted } from './test/renderApp'
import type { Reply } from './test/fakeServer'

// M6 H7: App-level tests drive the whole UI with real-time polling, so a slow machine
// needs more than vitest's 5 s default. No retries: a failing test still fails.
vi.setConfig({ testTimeout: 15_000 })

describe('page load and quota (D15, D25)', () => {
  it('shows the config screen at once and the waking message only after an early submit', async () => {
    const server = defaultServer()
    const health = deferred<Reply>()
    server.on('GET /status', () => health.promise)
    const user = renderApp()

    expect(screen.getByLabelText(/your document/i)).toBeEnabled()
    expect(screen.queryByText(/waking the server/i)).not.toBeInTheDocument()

    await submitDocument(user)
    expect(await screen.findByText('Waking the server - up to a minute on first visit')).toBeInTheDocument()
    expect(server.upload.sent).toBe(0)

    await act(async () => health.resolve(HEALTHY))
    expect(await screen.findByText('Uploading your document')).toBeInTheDocument()
    expect(server.upload.sent).toBe(1)
  })

  it('shows how many defenses are left', async () => {
    defaultServer()
    renderApp()
    expect(await screen.findByText('7 of 11 defenses left today')).toBeInTheDocument()
  })

  it('disables Start and says when more open, in Manila time, when none are left', async () => {
    const server = defaultServer()
    server.on('GET /quota', { status: 200, body: quota(0) })
    const user = renderApp()
    expect(await screen.findByText('No defenses left today. More at Fri 3:00 PM, Manila time.')).toBeInTheDocument()
    await chooseFile(user)
    expect(screen.getByRole('button', { name: /upload and convene/i })).toBeDisabled()
  })

  it('hides the quota line and lets the user proceed when /quota is 503', async () => {
    const server = defaultServer()
    server.on('GET /quota', { status: 503, body: { detail: "The demo's usage counter is waking up." } })
    const user = renderApp()
    await screen.findByRole('button', { name: /Recommended for a thesis/ }) // rosters loaded, so /quota has answered too
    expect(screen.queryByText(/defenses left/)).not.toBeInTheDocument()
    await chooseFile(user)
    expect(screen.getByRole('button', { name: /upload and convene/i })).toBeEnabled()
  })
})

describe('panel config (D14, D21, D22) and presets (M10 N7)', () => {
  const sentPanel = (server: ReturnType<typeof defaultServer>) => {
    const sent = server.upload.current?.form?.get('panel')
    return typeof sent === 'string' ? JSON.parse(sent) : null
  }

  it('selects the Recommended preset for the type and sends its panel', async () => {
    const server = defaultServer()
    const user = renderApp()
    const recommended = await screen.findByRole('button', { name: /Recommended for a thesis/ })
    expect(recommended).toHaveAttribute('aria-pressed', 'true')
    expect(recommended).toHaveTextContent('Methodology · Problem & Objectives · Results & Conclusions')
    // The fixture's thesis roster has no Technical Implementation or Statistical Analysis seat,
    // so the presets that need them are hidden, not sent.
    expect(screen.queryByRole('button', { name: /Technical-heavy/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Research-heavy/ })).not.toBeInTheDocument()
    await submitDocument(user)
    expect(sentPanel(server)).toMatchObject({
      defense_type: 'thesis',
      archetype_keys: ['methodology_expert', 'problem_objectives_reviewer', 'results_conclusions_reviewer'],
    })
  })

  it('lists the roster under Custom with labels, notes the Literature Specialist, and sends the chosen panel', async () => {
    const server = defaultServer()
    const user = renderApp()
    await screen.findByRole('button', { name: /Recommended for a thesis/ })
    await user.click(screen.getByRole('button', { name: /^Custom/ }))
    // Custom starts from the selected preset; the Literature Specialist isn't in it, and says why.
    expect(screen.getByRole('checkbox', { name: /Methodology Expert/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /Literature & Theory Specialist/ })).not.toBeChecked()
    expect(screen.getByText("Doesn't read the literature review in this version.")).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Other' }))
    expect(screen.getByRole('option', { name: 'Oral comps' })).toBeInTheDocument()
    expect(screen.queryByText('oral_comps')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Kind of panel'), 'Certification interview')
    // The fixture's certification roster seats no preset, so Custom is all there is.
    expect(screen.getByRole('button', { name: /^Custom/ })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('checkbox', { name: /Industry Practice Reviewer/ }))
    await user.click(screen.getByText(/Optional details/))
    await user.type(screen.getByLabelText(/domain/i), 'IT')
    await user.click(screen.getByRole('button', { name: '4' }))
    await submitDocument(user)

    expect(sentPanel(server)).toEqual({
      defense_type: 'other',
      other_subtype: 'certification_interview',
      domain: 'IT',
      topic: null,
      archetype_keys: ['technical_implementation_reviewer'],
      starting_difficulty: 4,
    })
  })

  it('takes a dropped PDF as the file (N3)', async () => {
    defaultServer()
    renderApp()
    const zone = (await screen.findByText(/Drop your PDF here/)).closest('label')
    if (zone === null) throw new Error('no drop zone')
    const file = new File(['%PDF-1.7'], 'dropped.pdf', { type: 'application/pdf' })
    fireEvent.drop(zone, { dataTransfer: { files: [file] } })
    expect(await screen.findByText('dropped.pdf')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upload and convene/i })).toBeEnabled()
  })
})

describe('upload, queue and embedding (D16)', () => {
  it('shows upload progress from XHR events', async () => {
    const server = defaultServer()
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    await submitDocument(user)
    await act(async () => server.upload.current?.progress(13, 26))
    expect(screen.getByRole('progressbar', { name: 'Upload progress' })).toHaveAttribute('aria-valuenow', '50')
    expect(screen.getByText('50%')).toBeInTheDocument()
  })

  it('shows the queue position, then embedding progress, then ready, polling every 2 s', async () => {
    const server = defaultServer()
    let state: unknown = { status: 'queued', queue_position: 1 }
    server.on('GET /sessions/abc', () => ({ status: 200, body: state }))
    const user = renderApp()
    await uploadAccepted(server, user, 2)
    expect(screen.getByText('In line - position 2')).toBeInTheDocument()

    await advance(1999)
    expect(server.count('GET /sessions/abc')).toBe(0)
    await advance(1)
    expect(await screen.findByText('In line - position 1')).toBeInTheDocument()

    state = { status: 'embedding', progress: 0.4 }
    await advance(2000)
    expect(await screen.findByText('Reading your document')).toBeInTheDocument()
    expect(screen.getByText('Large documents take about a minute.')).toBeInTheDocument()
    expect(screen.getByText('40%')).toBeInTheDocument()

    state = { status: 'ready', chunk_count: 92, defense: 'not_started' }
    await advance(2000)
    expect(await screen.findByText('Your document is ready')).toBeInTheDocument()
    const polls = server.count('GET /sessions/abc')
    await advance(10_000)
    expect(server.count('GET /sessions/abc')).toBe(polls) // polling stopped
  })

  it('shows the scanned-PDF message from error_code', async () => {
    const server = defaultServer()
    server.on('GET /sessions/abc', {
      status: 200,
      body: { status: 'failed', error: 'This PDF looks scanned: (0 characters).', error_code: 'scanned_pdf' },
    })
    const user = renderApp()
    await uploadAccepted(server, user)
    await advance(2000)
    expect(await screen.findByText("This PDF needs selectable text - scanned pages can't be read.")).toBeInTheDocument()
  })

  it('shows any other failure reason verbatim', async () => {
    const server = defaultServer()
    server.on('GET /sessions/abc', { status: 200, body: { status: 'failed', error: 'Something went wrong while processing this PDF.' } })
    const user = renderApp()
    await uploadAccepted(server, user)
    await advance(2000)
    expect(await screen.findByText('Something went wrong while processing this PDF.')).toBeInTheDocument()
  })

  it('pauses polling on a network error and resumes on Try again', async () => {
    const server = defaultServer()
    server.on('GET /sessions/abc', 'network')
    const user = renderApp()
    await uploadAccepted(server, user)
    await advance(2000)
    expect(await screen.findByText("Can't reach the server.")).toBeInTheDocument()
    await advance(10_000)
    expect(server.count('GET /sessions/abc')).toBe(1)
    server.on('GET /sessions/abc', { status: 200, body: { status: 'embedding', progress: 0.1 } })
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    await advance(2000)
    expect(await screen.findByText('Reading your document')).toBeInTheDocument()
  })
})

describe('upload errors (D16, D23, D24)', () => {
  it('blocks a file over 30 MB before uploading', async () => {
    const server = defaultServer()
    const user = renderApp()
    const big = pdf()
    Object.defineProperty(big, 'size', { value: 31 * 1024 * 1024 })
    await screen.findByText(/defenses left today/)
    await submitDocument(user, big)
    expect(await screen.findByText('That PDF is larger than the 30 MB limit.')).toBeInTheDocument()
    expect(server.upload.sent).toBe(0)
  })

  it.each([
    [413, 'That PDF has 300 pages; the limit is 250.'],
    [422, "That file isn't a PDF. Upload a .pdf document."],
  ])('shows a server %i message verbatim', async (status, detail) => {
    const server = defaultServer()
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    await submitDocument(user)
    await act(async () => server.upload.current?.respond(status, { detail }))
    expect(await screen.findByText(detail)).toBeInTheDocument()
  })

  it.each([
    ['per_ip_limit', "You've used your 3 defenses for today. More at Thu 3:00 PM, Manila time."],
    ['daily_cap', "Today's defenses are all used. More at Thu 3:00 PM, Manila time."],
    [undefined, 'No more defenses can start right now. More at Thu 3:00 PM, Manila time.'],
  ])('reads a 429 by its code (%s), with the reset in Manila time', async (code, text) => {
    const server = defaultServer()
    const user = renderApp()
    vi.setSystemTime(new Date('2026-10-22T05:00:00Z')) // 10:00 PM Pacific on the 21st
    await screen.findByText(/defenses left today/)
    await submitDocument(user)
    await act(async () =>
      server.upload.current?.respond(429, { detail: 'any wording', code, limit: 3 }, { 'Retry-After': String(2 * 3600) }),
    )
    expect(await screen.findByText(text)).toBeInTheDocument()
  })

  it('shows a 503 without Retry-After with a Try again button and never retries the upload itself', async () => {
    const server = defaultServer()
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    await submitDocument(user)
    const busy = 'The panel is busy with other documents right now. Please try again in a few minutes.'
    await act(async () => server.upload.current?.respond(503, { detail: busy }))
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(busy)).toBeInTheDocument()
    expect(within(alert).queryByText(/retrying in/)).not.toBeInTheDocument()
    await advance(120_000)
    expect(server.upload.sent).toBe(1)
    await user.click(within(alert).getByRole('button', { name: 'Try again' }))
    expect(server.upload.sent).toBe(2)
  })

  it('never auto-retries an upload even when a 503 has Retry-After', async () => {
    const server = defaultServer()
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    await submitDocument(user)
    await act(async () => server.upload.current?.respond(503, { detail: 'busy' }, { 'Retry-After': '5' }))
    await advance(60_000)
    expect(server.upload.sent).toBe(1)
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('shows "Can\'t reach the server" with Try again when the upload drops', async () => {
    const server = defaultServer()
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    await submitDocument(user)
    await act(async () => server.upload.current?.fail())
    expect(await screen.findByText("Can't reach the server.")).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(server.upload.sent).toBe(2)
  })
})

describe('expired session (D16, 404)', () => {
  it('says so, and the one button returns to config with the file cleared', async () => {
    const server = defaultServer()
    server.on('GET /sessions/abc', { status: 404, body: { detail: 'Session not found.' } })
    const user = renderApp()
    await uploadAccepted(server, user)
    await advance(2000)
    expect(await screen.findByText('This session expired - upload your document again.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Upload again' }))
    expect(screen.getByRole('button', { name: /upload and convene/i })).toBeDisabled() // no file chosen
    expect(screen.queryByText(/thesis\.pdf/)).not.toBeInTheDocument()
  })
})
