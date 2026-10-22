// Renders the real App, in StrictMode as main.tsx does, against a fake server.
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { vi } from 'vitest'
import App from '../App'
import { fakeServer, type FakeServer, type Reply } from './fakeServer'
import { panelOptions, pdf, quota } from './fixtures'

export const HEALTHY: Reply = { status: 200, body: { status: 'ok', model_loaded: true } }

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
  server.on('GET /health', HEALTHY)
  server.on('GET /quota', { status: 200, body: quota(7) })
  server.on('GET /panel-options', { status: 200, body: panelOptions })
  return server
}

export function renderApp() {
  // shouldAdvanceTime lets findBy* poll in real time while tests can still jump the clock.
  vi.useFakeTimers({ shouldAdvanceTime: true })
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

/** Sends a document and answers the upload with 202, leaving the session queued. */
export async function uploadAccepted(server: FakeServer, user: ReturnType<typeof userEvent.setup>, position = 1) {
  await screen.findByText(/defenses left today/)
  await submitDocument(user)
  await act(async () => server.upload.current?.respond(202, { session_id: 'abc', status: 'queued', queue_position: position }))
}
