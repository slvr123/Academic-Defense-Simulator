// M17: "Use your own Gemini key" and "How it works" on the start screen.
import { act, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HOW_IT_WORKS_ID, SOURCE_URL } from './components/HowItWorks'
import { KEY_HELP_URL, KEY_READY_TEXT, USED_UP_LEAD } from './components/OwnKeyPanel'
import { OWN_KEY_QUOTA_TEXT } from './components/QuotaLine'
import { lastAnswer, quota, report, startResponse } from './test/fixtures'
import { advance, defaultServer, renderApp, startDefense, submitDocument } from './test/renderApp'

vi.setConfig({ testTimeout: 15_000 })

const KEY = 'AIzaSy-test-own-key-M17-7731'
const INVALID = "Google rejected this key. Check for missing characters, or create a fresh one."
const keyToggle = () => screen.getByRole('button', { name: 'Use your own Gemini key' })
const keyInput = () => screen.queryByLabelText('Your Gemini API key')

function keyServer(remaining = 7) {
  const server = defaultServer()
  server.on('GET /quota', { status: 200, body: quota(remaining) })
  server.on('POST /keys/check', { status: 200, body: { ok: true } })
  server.on('GET /sessions/abc', { status: 200, body: { status: 'ready', chunk_count: 8 } })
  server.on('POST /sessions/abc/start', { status: 200, body: startResponse })
  server.on('POST /sessions/abc/answers', { status: 200, body: lastAnswer })
  server.on('GET /sessions/abc/report', { status: 200, body: report })
  server.on('DELETE /sessions/abc', { status: 204 })
  return server
}

async function enterKey(user: ReturnType<typeof renderApp>, key = KEY) {
  await user.type(keyInput() as HTMLElement, key)
  await user.click(screen.getByRole('button', { name: 'Use this key' }))
}

/** Every place a page could keep the key: both storages, the URL, and the page itself. */
function keyKeptAnywhere(key: string): string[] {
  const found: string[] = []
  for (const [name, storage] of [['localStorage', localStorage], ['sessionStorage', sessionStorage]] as const) {
    for (let i = 0; i < storage.length; i++) {
      const name_ = storage.key(i) ?? ''
      if (name_.includes(key) || (storage.getItem(name_) ?? '').includes(key)) found.push(name)
    }
  }
  if (window.location.href.includes(key)) found.push('URL')
  if (document.documentElement.outerHTML.includes(key)) found.push('DOM')
  return found
}

describe('use your own Gemini key (M17 Decision 1)', () => {
  it('is collapsed by default, next to the quota line, and opens from there', async () => {
    keyServer()
    const user = renderApp()
    await screen.findByText('7 of 11 defenses left today')
    expect(keyInput()).not.toBeInTheDocument()
    expect(keyToggle()).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText(USED_UP_LEAD)).not.toBeInTheDocument()
    await user.click(keyToggle())
    expect(keyToggle()).toHaveAttribute('aria-expanded', 'true')
    expect(keyInput()).toHaveAttribute('type', 'password')
    const help = screen.getByRole('link', { name: 'aistudio.google.com/apikey' })
    expect(help).toHaveAttribute('href', KEY_HELP_URL)
    expect(help.closest('p')).toHaveTextContent(
      'Free, about 60 seconds: aistudio.google.com/apikey. Sign in with Google, then Create API key. No card needed.',
    )
    await user.click(keyToggle())
    expect(keyInput()).not.toBeInTheDocument()
  })

  it('becomes the main suggestion when the free defenses are used up', async () => {
    keyServer(0)
    renderApp()
    expect(await screen.findByText(USED_UP_LEAD)).toBeInTheDocument()
    expect(keyInput()).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Use your own Gemini key' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upload and convene/i })).toBeDisabled()
  })

  it('checks the key in its header, then reads "Using your own key" and keeps it nowhere', async () => {
    const server = keyServer()
    const user = renderApp()
    await screen.findByText('7 of 11 defenses left today')
    await user.click(keyToggle())
    await enterKey(user)
    expect(await screen.findByText(KEY_READY_TEXT)).toBeInTheDocument()
    expect(server.headersOf('POST /keys/check')).toEqual({ 'x-gemini-key': KEY })
    expect(server.calls.filter((call) => call.includes(KEY))).toEqual([]) // never in a URL
    expect(screen.getByText(OWN_KEY_QUOTA_TEXT)).toBeInTheDocument()
    expect(screen.queryByText(/defenses left today/)).not.toBeInTheDocument()
    expect(keyInput()).not.toBeInTheDocument()
    expect(keyKeptAnywhere(KEY)).toEqual([])
  })

  it('sends the key once, in the upload header, and drops it after', async () => {
    const server = keyServer()
    const user = renderApp()
    await screen.findByText('7 of 11 defenses left today')
    await user.click(keyToggle())
    await enterKey(user)
    await screen.findByText(KEY_READY_TEXT)
    await submitDocument(user)
    const upload = server.upload.current
    expect(upload?.requestHeaders).toEqual({ 'X-Gemini-Key': KEY })
    const fields: string[] = []
    upload?.form?.forEach((value, name) => fields.push(name, typeof value === 'string' ? value : value.name))
    expect(fields.some((field) => field.includes(KEY))).toBe(false) // only the file and the panel go in the form
    await act(async () => upload?.respond(202, { session_id: 'abc', status: 'queued', queue_position: 1 }))
    await advance(2000)
    await startDefense(user)
    await screen.findByLabelText('Your answer')
    expect(keyKeptAnywhere(KEY)).toEqual([])
    await user.click(screen.getByRole('button', { name: 'End session' }))
    const dialog = await screen.findByRole('alertdialog')
    await user.click(within(dialog).getByRole('button', { name: /end session/i }))
    // Back on the start screen the key is gone: the free count is back, and a new upload sends no key.
    expect(await screen.findByText('7 of 11 defenses left today')).toBeInTheDocument()
    await submitDocument(user)
    expect(server.upload.current?.requestHeaders).toEqual({})
  })

  it('lets an own-key upload go when the free defenses are used up', async () => {
    keyServer(0)
    const user = renderApp()
    await screen.findByText(USED_UP_LEAD)
    await enterKey(user)
    await screen.findByText(KEY_READY_TEXT)
    expect(screen.getByText(OWN_KEY_QUOTA_TEXT)).toBeInTheDocument()
    await user.upload(screen.getByLabelText(/your document/i), new File(['%PDF'], 'paper.pdf', { type: 'application/pdf' }))
    expect(screen.getByRole('button', { name: /upload and convene/i })).toBeEnabled()
  })

  it("shows Google's refusal and keeps nothing", async () => {
    const server = keyServer()
    server.on('POST /keys/check', { status: 400, body: { detail: INVALID, code: 'invalid_key' } })
    const user = renderApp()
    await screen.findByText('7 of 11 defenses left today')
    await user.click(keyToggle())
    await enterKey(user, 'AIza-typo')
    expect(await screen.findByRole('alert')).toHaveTextContent(INVALID)
    expect(screen.getByText('7 of 11 defenses left today')).toBeInTheDocument()
    expect(screen.queryByText(KEY_READY_TEXT)).not.toBeInTheDocument()
  })

  it('can be removed before the upload, which then sends no key', async () => {
    const server = keyServer()
    const user = renderApp()
    await screen.findByText('7 of 11 defenses left today')
    await user.click(keyToggle())
    await enterKey(user)
    await user.click(await screen.findByRole('button', { name: 'Remove key' }))
    expect(screen.getByText('7 of 11 defenses left today')).toBeInTheDocument()
    await submitDocument(user)
    expect(server.upload.current?.requestHeaders).toEqual({})
  })
})

describe('own-key errors mid-defense (M17 Decision 4)', () => {
  const REJECTED = 'Your Gemini key was rejected partway through. Paste a fresh key to start a new defense.'
  const LIMITED = 'Your Gemini key hit its own limit. Wait a minute and try again, or come back after midnight Pacific.'

  it.each([
    [403, 'own_key_rejected', REJECTED],
    [429, 'own_key_limited', LIMITED],
  ])('a %i %s shows its own copy, not the panel being busy', async (status, code, text) => {
    const server = keyServer()
    server.on('POST /sessions/abc/answers', { status, body: { detail: text, code }, headers: { 'Retry-After': '60' } })
    const user = renderApp()
    await screen.findByText('7 of 11 defenses left today')
    await user.click(keyToggle())
    await enterKey(user)
    await screen.findByText(KEY_READY_TEXT)
    await submitDocument(user)
    await act(async () => server.upload.current?.respond(202, { session_id: 'abc', status: 'queued', queue_position: 1 }))
    await advance(2000)
    await startDefense(user)
    await user.type(await screen.findByLabelText('Your answer'), 'We adapted ISO 25010.')
    await user.click(screen.getByRole('button', { name: 'Submit answer' }))
    expect(await screen.findByText(text)).toBeInTheDocument()
    expect(screen.queryByText(/panel is busy/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Your answer')).toHaveValue('We adapted ISO 25010.') // the answer is kept to resend
  })
})

describe('how it works (M17 Decision 5)', () => {
  it('is on the start screen, with a header link that scrolls to it', async () => {
    keyServer()
    const user = renderApp()
    await screen.findByText('7 of 11 defenses left today')
    const section = screen.getByRole('region', { name: 'How it works' })
    expect(section).toHaveAttribute('id', HOW_IT_WORKS_ID)
    for (const title of ['RAG, turned around.', 'A panel, not a chatbot.', 'It adapts, quietly.', 'Measured, not guessed.']) {
      expect(within(section).getByText(title)).toBeInTheDocument()
    }
    expect(section).toHaveTextContent('Instead of searching your paper to answer your questions, the panel searches it to question you.')
    expect(section).not.toHaveTextContent(/harder as you do well|Most RAG apps/) // cut in M17: false, and unbacked
    expect(within(section).getByRole('link', { name: 'Source on GitHub' })).toHaveAttribute('href', SOURCE_URL)
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    const link = screen.getByRole('link', { name: 'How it works' })
    expect(link).toHaveAttribute('href', `#${HOW_IT_WORKS_ID}`)
    await user.click(link)
    expect(scrollIntoView).toHaveBeenCalledTimes(1)
    expect(window.location.hash).toBe('') // nothing added to the URL
  })

  it('is hidden during a defense', async () => {
    const server = keyServer()
    const user = renderApp()
    await screen.findByText('7 of 11 defenses left today')
    await submitDocument(user)
    await act(async () => server.upload.current?.respond(202, { session_id: 'abc', status: 'queued', queue_position: 1 }))
    await advance(2000)
    await startDefense(user)
    await screen.findByLabelText('Your answer')
    expect(screen.queryByRole('region', { name: 'How it works' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'How it works' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Use your own Gemini key' })).not.toBeInTheDocument()
  })
})
