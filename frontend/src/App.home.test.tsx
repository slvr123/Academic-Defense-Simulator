// M13 D0c: the wordmark goes back to the start screen, asking first wherever leaving ends
// a defense or an upload; and the Devil's Advocate is shown as the Terror Panelist.
import { act, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import realRecording from '../public/demo/anicheck-defense.json'
import { CLOSING_HOLD_MS } from './components/DefenseScreens'
import { DEMO_PATH } from './components/DemoReplay'
import { HOME_LABEL } from './components/SessionHeader'
import { lastAnswer, report, startResponse } from './test/fixtures'
import type { Reply } from './test/fakeServer'
import { advance, defaultServer, deferred, renderApp, startDefense, submitDocument, uploadAccepted } from './test/renderApp'

// M6 H7: App-level tests drive the whole UI with real-time polling, so a slow machine
// needs more than vitest's 5 s default. No retries: a failing test still fails.
vi.setConfig({ testTimeout: 15_000 })

const START_SCREEN = /Drop your PDF here/
const home = () => screen.getByRole('button', { name: HOME_LABEL })

function sessionServer() {
  const server = defaultServer()
  server.on('GET /sessions/abc', { status: 200, body: { status: 'ready', chunk_count: 8 } })
  server.on('POST /sessions/abc/start', { status: 200, body: startResponse })
  server.on('POST /sessions/abc/answers', { status: 200, body: lastAnswer })
  server.on('GET /sessions/abc/report', { status: 200, body: report })
  server.on('DELETE /sessions/abc', { status: 204 })
  return server
}

async function toQuestion(server = sessionServer()) {
  const user = renderApp()
  await uploadAccepted(server, user)
  await advance(2000)
  await startDefense(user)
  await screen.findByLabelText('Your answer')
  return { server, user }
}

function demoServer() {
  const server = defaultServer()
  server.on(`GET ${DEMO_PATH}`, { status: 200, body: realRecording })
  return server
}

describe('the wordmark goes back to the start screen (M13 D0c)', () => {
  it('is a focusable control on the start screen that does nothing there', async () => {
    const server = defaultServer()
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    const calls = server.calls.length
    expect(home()).toHaveAttribute('aria-current', 'page')
    expect(home()).toHaveTextContent('Academic Defense Simulator')
    await user.click(home())
    expect(screen.getByText(START_SCREEN)).toBeInTheDocument()
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(server.calls.length).toBe(calls)
  })

  it('goes straight from the debrief to the start screen', async () => {
    const server = sessionServer()
    const { user } = await toQuestion(server)
    await user.type(screen.getByLabelText('Your answer'), 'We adapted ISO 25010.')
    await user.click(screen.getByRole('button', { name: 'Submit answer' }))
    await screen.findByText('Your debrief')
    await user.click(home())
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(await screen.findByText(START_SCREEN)).toBeInTheDocument()
    expect(server.count('DELETE /sessions/abc')).toBe(0) // the defense was already over
  })

  it('goes straight from the recorded demo to the start screen, with no backend call', async () => {
    const server = demoServer()
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    await user.click(screen.getByRole('button', { name: /Read a recorded defense/ }))
    await user.click(await screen.findByRole('button', { name: 'Next' }))
    await screen.findByText(/asks/i, { selector: 'span' })
    const calls = server.calls.filter((call) => call !== `GET ${DEMO_PATH}`).length
    await user.click(home())
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(await screen.findByText(START_SCREEN)).toBeInTheDocument()
    expect(server.calls.filter((call) => call !== `GET ${DEMO_PATH}`).length).toBe(calls)
  })

  it('asks before leaving an upload; Cancel stays, and confirming deletes the session the upload makes', async () => {
    const server = sessionServer()
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    await submitDocument(user)
    await screen.findByText('Uploading your document')

    await user.click(home())
    const dialog = screen.getByRole('alertdialog', { name: 'End session?' })
    expect(within(dialog).getByText(/The panel stops here and this session is closed/)).toBeInTheDocument()
    expect(dialog).toContainElement(document.activeElement as HTMLElement) // the focus trap took focus
    await user.click(within(dialog).getByRole('button', { name: 'Keep going' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(screen.getByText('Uploading your document')).toBeInTheDocument()

    await user.click(home())
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'End session' }))
    expect(await screen.findByText(START_SCREEN)).toBeInTheDocument()
    // The upload lands after the user left: its session is deleted, never shown.
    await act(async () => server.upload.current?.respond(202, { session_id: 'abc', status: 'queued', queue_position: 1 }))
    await vi.waitFor(() => expect(server.count('DELETE /sessions/abc')).toBe(1))
    expect(screen.getByText(START_SCREEN)).toBeInTheDocument()
  })

  it('asks before leaving while the panel prepares, and confirming sends the DELETE', async () => {
    const server = sessionServer()
    const held = deferred<Reply>()
    server.on('POST /sessions/abc/start', () => held.promise)
    const user = renderApp()
    await uploadAccepted(server, user)
    await advance(2000)
    await startDefense(user)
    await screen.findByText(/The panel is reading your paper/)

    await user.click(home())
    await user.click(within(screen.getByRole('alertdialog', { name: 'End session?' })).getByRole('button', { name: 'End session' }))
    expect(await screen.findByText(START_SCREEN)).toBeInTheDocument()
    expect(server.count('DELETE /sessions/abc')).toBe(1)
  })

  it('asks before leaving a live defense: Cancel stays on the question, confirming sends the DELETE', async () => {
    const { server, user } = await toQuestion()

    await user.click(home())
    await user.click(within(screen.getByRole('alertdialog', { name: 'End session?' })).getByRole('button', { name: 'Keep going' }))
    expect(screen.getByLabelText('Your answer')).toBeInTheDocument()
    expect(server.count('DELETE /sessions/abc')).toBe(0)

    await user.click(home())
    await user.click(within(screen.getByRole('alertdialog', { name: 'End session?' })).getByRole('button', { name: 'End session' }))
    expect(await screen.findByText(START_SCREEN)).toBeInTheDocument()
    expect(server.count('DELETE /sessions/abc')).toBe(1)
  })

  it('works from the keyboard: Enter on the focused wordmark', async () => {
    const { server, user } = await toQuestion()
    home().focus()
    await user.keyboard('{Enter}')
    const dialog = screen.getByRole('alertdialog', { name: 'End session?' })
    await user.keyboard('{Escape}')
    expect(dialog).not.toBeInTheDocument()
    expect(home()).toHaveFocus() // the trap gives focus back
    await user.keyboard('{Enter}')
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'End session' }))
    expect(await screen.findByText(START_SCREEN)).toBeInTheDocument()
    expect(server.count('DELETE /sessions/abc')).toBe(1)
  })
})

describe('the Terror Panelist (M13 D0c)', () => {
  it('is named on the start screen, with its role, and in the custom-panel hint', async () => {
    defaultServer()
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    expect(screen.getByText('Every panel also seats a Terror Panelist, who speaks last and challenges your earlier answers.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^Custom/ }))
    expect(screen.getByText(/the Terror Panelist always joins/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/devil.s advocate/i)
  })

  it("never shows \"Devil's Advocate\" anywhere in the recorded demo, start to debrief", async () => {
    demoServer()
    const user = renderApp()
    await screen.findByText(/defenses left today/)
    await user.click(screen.getByRole('button', { name: /Read a recorded defense/ }))
    let steps = 0
    for (;;) {
      await advance(50)
      expect(document.body.textContent).not.toMatch(/devil.s advocate/i)
      if (screen.queryByRole('region', { name: /closes$/ }) !== null) {
        await advance(CLOSING_HOLD_MS) // M15: a recorded closing line holds as it does live
        continue
      }
      const next = screen.queryByRole('button', { name: 'Next' })
      if (next === null) break
      await user.click(next)
      steps += 1
    }
    expect(steps).toBeGreaterThanOrEqual(6) // every question and the debrief were seen
    expect(screen.getByText('Your debrief')).toBeInTheDocument()
    expect(screen.getAllByText(/Terror Panelist/).length).toBeGreaterThan(0)
  })

  it('names the seated panelist in a live session as the API sends it', async () => {
    await toQuestion()
    expect(screen.getByText('Terror Panelist')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/devil.s advocate/i)
  })
})
