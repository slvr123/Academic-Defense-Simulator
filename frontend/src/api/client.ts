// The only module that talks to the network. Every call resolves to the typed body
// or a typed ApiError; status codes stop here (M3 decisions D5).
import type {
  AnswerResponse,
  HealthResponse,
  LimitCode,
  PanelConfig,
  PanelOptions,
  QuotaStatus,
  ReportResponse,
  SessionCreated,
  SessionState,
  StartResponse,
} from './types'

export type ApiError =
  | { kind: 'network' }
  | { kind: 'notFound'; message: string } // 404: the session expired or never existed
  | { kind: 'conflict'; message: string } // 409: a request for this session is already running
  | { kind: 'turnFailed'; message: string } // 502: nothing was recorded, send the same request again
  | { kind: 'busy'; message: string; retryAfterS: number | null } // 503
  | { kind: 'tooLarge'; message: string } // 413: too many bytes or too many pages
  | { kind: 'invalid'; message: string } // 422
  | { kind: 'limited'; code: LimitCode | null; message: string; retryAfterS: number | null } // 429
  | { kind: 'unexpected'; message: string }

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiError }

// A Record over the generated union: the build fails if the backend adds a code.
const LIMIT_CODES: Record<LimitCode, true> = { per_ip_limit: true, daily_cap: true }

function isLimitCode(value: unknown): value is LimitCode {
  return typeof value === 'string' && Object.hasOwn(LIMIT_CODES, value)
}

function baseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL.replace(/\/+$/, '')
}

function field(body: unknown, name: string): unknown {
  return typeof body === 'object' && body !== null ? (body as Record<string, unknown>)[name] : undefined
}

/** FastAPI's `detail`: text, or on a 422 a list of validation errors with a `msg` each. */
function readDetail(body: unknown, fallback: string): string {
  const detail = field(body, 'detail')
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => field(item, 'msg')).filter((msg) => typeof msg === 'string')
    if (messages.length > 0) return messages.join('\n')
  }
  return fallback
}

export function parseRetryAfter(header: string | null): number | null {
  if (header === null || !/^\d+$/.test(header.trim())) return null
  return Number(header.trim())
}

export function toApiError(status: number, body: unknown, retryAfter: string | null): ApiError {
  const message = readDetail(body, `The server answered with an unexpected error (${status}).`)
  switch (status) {
    case 404:
      return { kind: 'notFound', message }
    case 409:
      return { kind: 'conflict', message }
    case 413:
      return { kind: 'tooLarge', message }
    case 422:
      return { kind: 'invalid', message }
    case 429: {
      const code = field(body, 'code')
      return { kind: 'limited', code: isLimitCode(code) ? code : null, message, retryAfterS: parseRetryAfter(retryAfter) }
    }
    case 502:
      return { kind: 'turnFailed', message }
    case 503:
      return { kind: 'busy', message, retryAfterS: parseRetryAfter(retryAfter) }
    default:
      return { kind: 'unexpected', message }
  }
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}

async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<ApiResult<T>> {
  let response: Response
  try {
    response = await fetch(`${baseUrl()}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    return { ok: false, error: { kind: 'network' } }
  }
  const text = await response.text().catch(() => '')
  const parsed = parseJson(text)
  if (!response.ok) {
    return { ok: false, error: toApiError(response.status, parsed, response.headers.get('Retry-After')) }
  }
  if (parsed === undefined) return { ok: false, error: { kind: 'unexpected', message: 'The server sent a reply that could not be read.' } }
  // The one place a body is trusted to match its type: the types are generated from the
  // same Pydantic models that produced it.
  return { ok: true, data: parsed as T }
}

const sessionPath = (sessionId: string) => `/sessions/${encodeURIComponent(sessionId)}`

export const getHealth = () => request<HealthResponse>('GET', '/health')
export const getQuota = () => request<QuotaStatus>('GET', '/quota')
export const getPanelOptions = () => request<PanelOptions>('GET', '/panel-options')
export const getSession = (sessionId: string) => request<SessionState>('GET', sessionPath(sessionId))
export const startDefense = (sessionId: string) => request<StartResponse>('POST', `${sessionPath(sessionId)}/start`)
export const submitAnswer = (sessionId: string, text: string) =>
  request<AnswerResponse>('POST', `${sessionPath(sessionId)}/answers`, { text })
export const getReport = (sessionId: string) => request<ReportResponse>('GET', `${sessionPath(sessionId)}/report`)

/**
 * POST /sessions. XMLHttpRequest, not fetch, because only XHR reports upload progress,
 * and a 26 MB PDF on a slow mobile connection needs a real progress bar (D5).
 */
export function createSession(
  file: File,
  panel: PanelConfig,
  onProgress: (loaded: number, total: number) => void,
): Promise<ApiResult<SessionCreated>> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${baseUrl()}/sessions`)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded, event.total)
    }
    xhr.onload = () => {
      const parsed = parseJson(xhr.responseText)
      if (xhr.status === 202 && parsed !== undefined) {
        resolve({ ok: true, data: parsed as SessionCreated })
      } else {
        resolve({ ok: false, error: toApiError(xhr.status, parsed, xhr.getResponseHeader('Retry-After')) })
      }
    }
    xhr.onerror = () => resolve({ ok: false, error: { kind: 'network' } })
    xhr.ontimeout = xhr.onerror
    const form = new FormData()
    form.append('file', file)
    form.append('panel', JSON.stringify(panel))
    xhr.send(form)
  })
}
