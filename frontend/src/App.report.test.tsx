// The debrief (D13, M8 K8), reached through a whole defense against the scripted server.
import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { lastAnswer, report, startResponse } from './test/fixtures'
import { advance, defaultServer, renderApp, startDefense, uploadAccepted } from './test/renderApp'

// M6 H7: App-level tests drive the whole UI with real-time polling, so a slow machine
// needs more than vitest's 5 s default. No retries: a failing test still fails.
vi.setConfig({ testTimeout: 15_000 })

async function toReport(body = report) {
  const server = defaultServer()
  server.on('GET /sessions/abc', { status: 200, body: { status: 'ready', chunk_count: 8 } })
  server.on('POST /sessions/abc/start', { status: 200, body: startResponse })
  server.on('POST /sessions/abc/answers', { status: 200, body: lastAnswer })
  server.on('GET /sessions/abc/report', { status: 200, body })
  const user = renderApp()
  await uploadAccepted(server, user)
  await advance(2000)
  await startDefense(user)
  await user.type(await screen.findByLabelText('Your answer'), 'We adapted ISO 25010.')
  await user.click(screen.getByRole('button', { name: 'Submit answer' }))
  await screen.findByText('Your debrief')
  return { server, user }
}

describe('the report', () => {
  it('leads with the backend’s overall_raw on 100, then the averages, the narrative and each panelist (M8 K8)', async () => {
    await toReport()
    const overall = screen.getByRole('region', { name: 'Overall' })
    // overall_raw 3.5 -> 70; the averages 3.5, 3, 4 -> 70, 60, 80 (v1.2.1)
    expect(within(overall).getByText('70', { exact: false, selector: 'p' })).toHaveTextContent('70/100')
    expect(within(overall).getAllByText('70/100')).toHaveLength(1) // clarity; the headline's /100 is its own span
    expect(within(overall).getByText('60/100')).toBeInTheDocument()
    expect(within(overall).getByText('80/100')).toBeInTheDocument()
    expect(within(overall).getByText(/The scale floor is 20\./)).toBeInTheDocument()
    expect(screen.getByText('You grounded most answers in the document.')).toBeInTheDocument()
    expect(screen.getByText('· 1 turn(s)')).toBeInTheDocument()
    expect(screen.getByText('No escalation moments this session')).toBeInTheDocument()
  })

  it('gives each question a card: scores, answer, highlighted passage, the gap and a stronger answer (M8 K8)', async () => {
    await toReport()
    const cards = screen.getByRole('region', { name: 'Question by question' })
    const card = within(cards).getByText('How did you validate your survey instrument?').closest('details')
    if (card === null) throw new Error('no question card')
    expect(card).toHaveAttribute('open') // the first card starts open
    expect(within(card).getByText('73/100')).toBeInTheDocument() // this turn's overall: 11 / 3
    expect(within(card).getByText('We adapted ISO 25010.')).toBeInTheDocument()
    expect(within(card).getByText('adapted from ISO/IEC 25010', { selector: 'mark' })).toBeInTheDocument()
    expect(within(card).getByText('PDF pages 12–13')).toBeInTheDocument()
    expect(within(card).getByText('No reliability coefficient reported.')).toBeInTheDocument()
    expect(within(card).getByText(/Report Cronbach’s alpha/)).toBeInTheDocument()

    const transcript = screen.getByRole('region', { name: 'Full transcript' })
    expect(within(transcript).getByText('How did you validate your survey instrument?')).toBeInTheDocument()
    expect(within(transcript).getByText('We adapted ISO 25010.')).toBeInTheDocument()
  })

  it('says so when the narrative or suggestions fell back', async () => {
    await toReport({
      ...report,
      report: { ...report.report, narrative: null, narrative_fallback_used: true, answer_suggestions: [], suggestions_fallback_used: true },
    })
    expect(screen.getByText('Narrative unavailable this session')).toBeInTheDocument()
    expect(screen.getByText('Suggestions unavailable this session')).toBeInTheDocument()
  })

  it('starts a new defense with the file cleared', async () => {
    const { user } = await toReport()
    await user.click(screen.getByRole('button', { name: 'Start a new defense' }))
    expect(screen.getByRole('button', { name: /upload and convene/i })).toBeDisabled()
  })

  it('saves as PDF through the print dialog, with every question card expanded for the print (M10 N11)', async () => {
    const print = vi.fn()
    vi.stubGlobal('print', print)
    const second = { ...report.transcript[0], turn: 2, question: 'Why thirty respondents?' }
    await toReport({ ...report, transcript: [...report.transcript, second] })
    const cards = within(screen.getByRole('region', { name: 'Question by question' }))
    const closed = cards.getByText('Why thirty respondents?').closest('details')
    expect(closed).not.toHaveAttribute('open')

    screen.getByRole('button', { name: 'Save as PDF' }).click()
    expect(print).toHaveBeenCalledOnce()
    window.dispatchEvent(new Event('beforeprint'))
    expect(closed).toHaveAttribute('open')
    window.dispatchEvent(new Event('afterprint'))
    expect(closed).not.toHaveAttribute('open') // back as it was
    expect(screen.getByRole('button', { name: 'Save as PDF' })).toHaveClass('print:hidden')
    expect(screen.getByRole('button', { name: 'Start a new defense' })).toHaveClass('print:hidden')
  })
})
