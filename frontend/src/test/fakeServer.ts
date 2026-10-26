// A scripted backend for tests: fetch and XMLHttpRequest are replaced with fakes that
// answer from per-route handlers, so tests drive the real client and the real UI.
import { vi } from 'vitest'

// `bytes` sends a binary body as is (the /tts audio); `body` is sent as JSON.
export type Reply = { status: number; body?: unknown; bytes?: Uint8Array<ArrayBuffer>; headers?: Record<string, string> } | 'network'
type Handler = (body: unknown) => Reply | Promise<Reply>

export const API = 'http://api.test'

export type FakeServer = {
  /** Set the handler for "METHOD /path"; later calls replace earlier ones. */
  on: (route: string, handler: Handler | Reply) => void
  /** Every request made, as "METHOD /path". */
  calls: string[]
  count: (route: string) => number
  upload: FakeUpload
}

export type FakeUpload = {
  /** The pending upload, once the client has sent one. */
  current: FakeXhr | null
  sent: number
}

export class FakeXhr {
  static server: { upload: FakeUpload; calls: string[] } | null = null
  status = 0
  responseText = ''
  upload: { onprogress: ((event: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = {
    onprogress: null,
  }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  ontimeout: (() => void) | null = null
  form: FormData | null = null
  private headers: Record<string, string> = {}
  private path = ''

  open(method: string, url: string): void {
    this.path = `${method} ${url.replace(API, '')}`
  }

  send(form: FormData): void {
    this.form = form
    const server = FakeXhr.server
    if (server === null) throw new Error('FakeXhr used outside fakeServer()')
    server.calls.push(this.path)
    server.upload.current = this
    server.upload.sent += 1
  }

  getResponseHeader(name: string): string | null {
    return this.headers[name.toLowerCase()] ?? null
  }

  progress(loaded: number, total: number): void {
    this.upload.onprogress?.({ lengthComputable: true, loaded, total })
  }

  respond(status: number, body: unknown, headers: Record<string, string> = {}): void {
    this.status = status
    this.responseText = JSON.stringify(body)
    this.headers = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]))
    this.onload?.()
  }

  fail(): void {
    this.onerror?.()
  }
}

/** Rejects as fetch does once `signal` aborts, whether or not the handler has answered. */
function abortable<T>(reply: Promise<T>, signal: AbortSignal | null | undefined): Promise<T> {
  if (!signal) return reply
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(new DOMException('The operation was aborted.', 'AbortError'))
    if (signal.aborted) return abort()
    signal.addEventListener('abort', abort, { once: true })
    reply.then(resolve, reject)
  })
}

export function fakeServer(): FakeServer {
  const handlers = new Map<string, Handler>()
  const calls: string[] = []
  const upload: FakeUpload = { current: null, sent: 0 }

  const fetchFake = vi.fn(async (input: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const route = `${method} ${input.replace(API, '')}`
    calls.push(route)
    const handler = handlers.get(route)
    if (handler === undefined) throw new Error(`no fake handler for ${route}`)
    const body: unknown = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    const reply = await abortable(Promise.resolve(handler(body)), init?.signal)
    if (reply === 'network') throw new TypeError('Failed to fetch')
    const sent = reply.bytes ?? (reply.body === undefined ? '' : JSON.stringify(reply.body))
    return new Response(sent, { status: reply.status, headers: reply.headers })
  })

  vi.stubGlobal('fetch', fetchFake)
  FakeXhr.server = { upload, calls }
  vi.stubGlobal('XMLHttpRequest', FakeXhr)

  return {
    on: (route, handler) => handlers.set(route, typeof handler === 'function' ? handler : () => handler),
    calls,
    count: (route) => calls.filter((call) => call === route).length,
    upload,
  }
}
