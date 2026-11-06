// M16: the panel-language choice (Decision 6). The flag hides it, the choice travels in
// the upload's panel config, the header names it, and a Taglish panel's voice carries a note.
import { act, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TAGLISH_VOICE_NOTE } from './components/VoiceGate'
import { sessionSubtitle } from './lib/labels'
import { startResponse } from './test/fixtures'
import type { FakeServer } from './test/fakeServer'
import { advance, defaultServer, renderApp, startDefense, submitDocument } from './test/renderApp'

vi.setConfig({ testTimeout: 15_000 })

function server(taglishMode: boolean): FakeServer {
  const fake = defaultServer()
  fake.on('GET /status', { status: 200, body: { status: 'ok', model_loaded: true, tts_available: true, taglish_mode: taglishMode } })
  fake.on('GET /sessions/abc', { status: 200, body: { status: 'ready', chunk_count: 8, defense: 'not_started' } })
  fake.on('POST /sessions/abc/start', { status: 200, body: startResponse })
  return fake
}

const languageGroup = () => screen.queryByRole('group', { name: 'Panel language' })

/** The panel config the upload sent, as the backend reads it. */
function sentPanel(fake: FakeServer): { panel_language: string } {
  const raw = fake.upload.current?.form?.get('panel')
  if (typeof raw !== 'string') throw new Error('the upload sent no panel field')
  return JSON.parse(raw)
}

async function upload(fake: FakeServer, user: ReturnType<typeof renderApp>, language?: 'Taglish' | 'English') {
  await advance(0)
  await screen.findByText(/defenses left today/)
  if (language) await user.click(within(languageGroup()!).getByRole('button', { name: language }))
  await submitDocument(user)
  await act(async () => fake.upload.current?.respond(202, { session_id: 'abc', status: 'queued', queue_position: 1 }))
  await advance(2000)
}

describe('the panel language (M16)', () => {
  it('is hidden with ADS_TAGLISH_MODE off, and the upload says English', async () => {
    const fake = server(false)
    const user = renderApp()
    await advance(0)
    await screen.findByText(/defenses left today/) // /status has answered by now
    expect(languageGroup()).toBeNull()
    expect(screen.queryByText('Taglish')).toBeNull()
    await upload(fake, user)
    expect(sentPanel(fake).panel_language).toBe('english')
  })

  it('is offered with the flag on, English first and pressed', async () => {
    server(true)
    renderApp()
    await advance(0)
    const group = await screen.findByRole('group', { name: 'Panel language' })
    const [english, taglish] = within(group).getAllByRole('button')
    expect(english).toHaveTextContent('English')
    expect(english).toHaveAttribute('aria-pressed', 'true')
    expect(taglish).toHaveTextContent('Taglish')
    expect(taglish).toHaveAttribute('aria-pressed', 'false')
  })

  it('sends Taglish, names it in the header, and notes that the voice may mispronounce Filipino', async () => {
    const fake = server(true)
    const user = renderApp()
    await upload(fake, user, 'Taglish')
    expect(sentPanel(fake).panel_language).toBe('taglish')

    expect(await screen.findByText(TAGLISH_VOICE_NOTE)).toBeInTheDocument() // the ready screen's voice choice
    await startDefense(user)
    await advance(0)
    expect(screen.getByText(`${startResponse.topic} · Thesis defense · Taglish`)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^Voice: .* Change voice$/ }))
    expect(screen.getByText(TAGLISH_VOICE_NOTE)).toBeInTheDocument() // the header's voice menu
  })

  it('leaves an English panel as before: no label, no voice note', async () => {
    const fake = server(true)
    const user = renderApp()
    await upload(fake, user, 'English')
    expect(sentPanel(fake).panel_language).toBe('english')
    await startDefense(user)
    await advance(0)
    expect(screen.getByText(`${startResponse.topic} · Thesis defense`)).toBeInTheDocument()
    expect(screen.queryByText(/Taglish/)).toBeNull()
    await user.click(screen.getByRole('button', { name: /^Voice: .* Change voice$/ }))
    expect(screen.queryByText(TAGLISH_VOICE_NOTE)).toBeNull()
  })
})

describe('sessionSubtitle', () => {
  it('adds the language only for Taglish', () => {
    expect(sessionSubtitle('AniCheck', { defense_type: 'capstone', panel_language: 'english' })).toBe('AniCheck · Capstone defense')
    expect(sessionSubtitle('AniCheck', { defense_type: 'thesis', panel_language: 'taglish' })).toBe('AniCheck · Thesis defense · Taglish')
  })
})
