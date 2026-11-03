// Renders the real App, in StrictMode as main.tsx does, against a fake server.
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { vi } from 'vitest'
import App from '../App'
import { fakeServer, type FakeServer, type Reply } from './fakeServer'
import { panelOptions, pdf, quota } from './fixtures'

export const HEALTHY: Reply = { status: 200, body: { status: 'ok', model_loaded: true, tts_available: true } }

export function deferred<T>() {
  let resolve: (value: T) => void = () => {}
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

/** A server that is awake, with 7 of 11 defenses left and the fixture rosters. */
export function defaultServer(): FakeServer {
  const server = fakeServer()
  server.on('GET /status', HEALTHY)
  server.on('GET /quota', { status: 200, body: quota(7) })
  server.on('GET /panel-options', { status: 200, body: panelOptions })
  return server
}

/** `clock: 'strict'` (M13 D2): the fake clock moves only when the test advances it, so a
 * test that counts timer-driven requests can't be pushed past a deadline by a slow
 * machine. findBy* can't poll on that clock: use advance() and then getBy*. */
export function renderApp({ clock = 'real-time' }: { clock?: 'real-time' | 'strict' } = {}) {
  // shouldAdvanceTime lets findBy* poll in real time while tests can still jump the clock;
  // the price is that real time leaks into the fake clock.
  vi.useFakeTimers(clock === 'strict' ? {} : { shouldAdvanceTime: true })
  // Testing Library drains each user-event call with a setTimeout(0) that it advances only
  // for Jest's fake timers; on a strict clock that timer would never fire. Presenting
  // Vitest's clock as Jest's lets it advance that 0 ms. setup.ts unstubs it after the test.
  if (clock === 'strict') vi.stubGlobal('jest', { advanceTimersByTime: (ms: number) => vi.advanceTimersByTime(ms) })
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
  render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
  return user
}

export async function advance(ms: number) {
  await act(() => vi.advanceTimersByTimeAsync(ms))
}

export async function chooseFile(user: ReturnType<typeof userEvent.setup>, file: File = pdf()) {
  await user.upload(screen.getByLabelText(/your document/i), file)
}

export async function submitDocument(user: ReturnType<typeof userEvent.setup>, file: File = pdf()) {
  await chooseFile(user, file)
  await user.click(screen.getByRole('button', { name: /upload and convene/i }))
}

/** The ready screen's voice choice (E9), then Start. Tests that aren't about voice choose none. */
export async function startDefense(user: ReturnType<typeof userEvent.setup>, voice: RegExp = /^No voice/) {
  await user.click(await screen.findByRole('radio', { name: voice }))
  await user.click(screen.getByRole('button', { name: 'Start the defense' }))
}

/** Sends a document and answers the upload with 202, leaving the session queued. */
export async function uploadAccepted(server: FakeServer, user: ReturnType<typeof userEvent.setup>, position = 1) {
  await advance(0) // the page-load calls answer; on a strict clock findBy* would not wait for them
  await screen.findByText(/defenses left today/)
  await submitDocument(user)
  await act(async () => server.upload.current?.respond(202, { session_id: 'abc', status: 'queued', queue_position: position }))
}
