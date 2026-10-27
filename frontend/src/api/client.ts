// The only module that talks to the network. Every call resolves to the typed body
// or a typed ApiError; status codes stop here (M3 decisions D5).
import type {
  AnswerResponse,
  CurrentQuestion,
  ErrorCode,
  HealthResponse,
  LimitCode,
  PanelConfig,
  PanelOptions,
  QuotaStatus,
  ReportResponse,
  SessionCreated,
  SessionState,
  StartResponse,
  TtsErrorCode,
} from './types'

export type ApiError =
  | { kind: 'network' }
  | { kind: 'notFound'; message: string } // 404: the session expired or never existed
  | { kind: 'conflict'; message: string } // 409: a request for this session is already running
  | { kind: 'stale'; message: string } // 409 stale_question: that question isn't open any more (E13)
  | { kind: 'alreadyStarted'; message: string } // 409 already_started: /start after the first answer (M6 H4)
  | { kind: 'turnFailed'; message: string } // 502: nothing was recorded, send the same request again
  | { kind: 'busy'; message: string; retryAfterS: number | null } // 503
  | { kind: 'tooLarge'; message: string } // 413: too many bytes or too many pages
  | { kind: 'invalid'; message: string } // 422
  // 429; limit: the starts a network gets per day, sent with per_ip_limit (M6 H6)
  | { kind: 'limited'; code: LimitCode | null; limit: number | null; message: string; retryAfterS: number | null }
  | { kind: 'tts'; code: TtsErrorCode; message: string } // /tts's coded 429, 502 and 503 (E3)
  | { kind: 'unexpected'; message: string }

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiError }

// A Record over the generated union: the build fails if the backend adds a code
// this client hasn't placed.
const ERROR_CODES: Record<ErrorCode, 'limit' | 'tts' | 'stale' | 'started'> = {
  per_ip_limit: 'limit',
  daily_cap: 'limit',
  tts_limit: 'tts',
  tts_failed: 'tts',
  tts_unavailable: 'tts',
  stale_question: 'stale',
  already_started: 'started',
}

function isErrorCode(value: unknown): value is ErrorCode {
  return typeof value === 'string' && Object.hasOwn(ERROR_CODES, value)
}

function errorCode(body: unknown): ErrorCode | null {
  const code = field(body, 'code')
  return isErrorCode(code) ? code : null
}

function isLimitCode(code: ErrorCode | null): code is LimitCode {
  return code !== null && ERROR_CODES[code] === 'limit'
}

function isTtsCode(code: ErrorCode | null): code is TtsErrorCode {
  return code !== null && ERROR_CODES[code] === 'tts'
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
  const code = errorCode(body)
  if (isTtsCode(code)) return { kind: 'tts', code, message }
  switch (status) {
    case 404:
      return { kind: 'notFound', message }
    case 409:
      if (code === 'stale_question') return { kind: 'stale', message }
      return code === 'already_started' ? { kind: 'alreadyStarted', message } : { kind: 'conflict', message }
    case 413:
      return { kind: 'tooLarge', message }
    case 422:
      return { kind: 'invalid', message }
    case 429: {
      const limit = field(body, 'limit')
      return {
        kind: 'limited',
        code: isLimitCode(code) ? code : null,
        limit: typeof limit === 'number' && Number.isInteger(limit) ? limit : null,
        message,
        retryAfterS: parseRetryAfter(retryAfter),
      }
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

// /status, not /health: EasyPrivacy blocks `||onrender.com/health` (M3b E2).
export const getStatus = () => request<HealthResponse>('GET', '/status')
export const getQuota = () => request<QuotaStatus>('GET', '/quota')
export const getPanelOptions = () => request<PanelOptions>('GET', '/panel-options')
export const getSession = (sessionId: string) => request<SessionState>('GET', sessionPath(sessionId))
export const startDefense = (sessionId: string) => request<StartResponse>('POST', `${sessionPath(sessionId)}/start`)
/** `turn` is the question being answered, so a resend after a lost response replays it (E13). */
export const submitAnswer = (sessionId: string, text: string, turn: number) =>
  request<AnswerResponse>('POST', `${sessionPath(sessionId)}/answers`, { text, turn })
export const getReport = (sessionId: string) => request<ReportResponse>('GET', `${sessionPath(sessionId)}/report`)
/** Where the defense is now: the open question, or done (M6 H5). */
export const getCurrent = (sessionId: string) => request<CurrentQuestion>('GET', `${sessionPath(sessionId)}/current`)

/**
 * POST /tts: one question's audio, read as a Blob of WAV bytes rather than text.
 * `signal` cancels it when the turn moves on (E7); a cancelled request resolves to a
 * network error, and callers check `signal.aborted` before acting on any result.
 */
export async function fetchTts(sessionId: string, turn: number, signal: AbortSignal): Promise<ApiResult<Blob>> {
  let response: Response
  try {
    response = await fetch(`${baseUrl()}/tts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, turn }),
      signal,
    })
  } catch {
    return { ok: false, error: { kind: 'network' } }
  }
  if (!response.ok) {
    const parsed = parseJson(await response.text().catch(() => ''))
    return { ok: false, error: toApiError(response.status, parsed, response.headers.get('Retry-After')) }
  }
  try {
    return { ok: true, data: await response.blob() }
  } catch {
    return { ok: false, error: { kind: 'network' } } // the body stopped arriving, or was cancelled
  }
}

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
