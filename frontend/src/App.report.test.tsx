// The basic report (D13), reached through a whole defense against the scripted server.
import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { lastAnswer, report, startResponse } from './test/fixtures'
import { advance, defaultServer, renderApp, uploadAccepted } from './test/renderApp'

async function toReport(body = report) {
  const server = defaultServer()
  server.on('GET /sessions/abc', { status: 200, body: { status: 'ready', chunk_count: 8 } })
  server.on('POST /sessions/abc/start', { status: 200, body: startResponse })
  server.on('POST /sessions/abc/answers', { status: 200, body: lastAnswer })
  server.on('GET /sessions/abc/report', { status: 200, body })
  const user = renderApp()
  await uploadAccepted(server, user)
  await advance(2000)
  await user.click(await screen.findByRole('button', { name: 'Start the defense' }))
  await user.type(await screen.findByLabelText('Your answer'), 'We adapted ISO 25010.')
  await user.click(screen.getByRole('button', { name: 'Send answer' }))
  await screen.findByText('Your defense report')
  return { server, user }
}

describe('the report', () => {
  it('shows the averages on 100, the narrative, each panelist, suggestions and the transcript with sources', async () => {
    await toReport()
    const overall = screen.getByText('Overall', { selector: 'h3' }).closest('section')
    if (overall === null) throw new Error('no overall section')
    // 3.5, 3, 4 -> 70, 60, 80, rescaled after averaging (v1.2.1)
    expect(within(overall).getByText('70/100')).toBeInTheDocument()
    expect(within(overall).getByText('60/100')).toBeInTheDocument()
    expect(within(overall).getByText('80/100')).toBeInTheDocument()
    expect(within(overall).getByText(/The scale floor is 20\./)).toBeInTheDocument()
    expect(screen.getByText('You grounded most answers in the document.')).toBeInTheDocument()
    expect(screen.getByText('· 1 turn(s)')).toBeInTheDocument()
    expect(screen.getByText(/Report Cronbach’s alpha/)).toBeInTheDocument()
    expect(screen.getByText('No escalation moments this session')).toBeInTheDocument()

    const transcript = screen.getByText('Transcript').closest('section')
    if (transcript === null) throw new Error('no transcript')
    expect(within(transcript).getByText('How did you validate your survey instrument?')).toBeInTheDocument()
    expect(within(transcript).getByText('We adapted ISO 25010.')).toBeInTheDocument()
    expect(within(transcript).getByText(/Source passage for turn 1/)).toBeInTheDocument()
    expect(within(transcript).getByText('Pages 12-13')).toBeInTheDocument()
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
})
