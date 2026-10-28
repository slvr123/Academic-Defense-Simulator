// Stand-ins for what jsdom doesn't implement: speechSynthesis, HTMLMediaElement playback,
// and object URLs. Each records what the app asked of it, so tests assert on that.
import { vi } from 'vitest'

export class FakeUtterance {
  voice: { name: string } | null = null
  pitch = 1
  rate = 1
  onstart: (() => void) | null = null
  onend: (() => void) | null = null
  onerror: ((event: { error: string }) => void) | null = null
  text: string
  constructor(text: string) {
    this.text = text
  }
}

/** The voices a stock Windows Chrome offers in English, in the order it lists them. */
export const CHROME_VOICES = [
  'Microsoft David - English (United States)',
  'Microsoft Mark - English (United States)',
  'Microsoft Zira - English (United States)',
  'Google US English',
  'Google UK English Female',
  'Google UK English Male',
]

export function fakeSpeech(voiceNames: string[] = CHROME_VOICES) {
  const all: FakeUtterance[] = []
  const synth = {
    speaking: false,
    getVoices: () => voiceNames.map((name) => ({ name })),
    speak: vi.fn((utterance: FakeUtterance) => all.push(utterance)),
    cancel: vi.fn(),
    resume: vi.fn(),
    addEventListener: vi.fn(),
  }
  vi.stubGlobal('speechSynthesis', synth)
  vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance)
  return {
    synth,
    /** What was spoken, without the empty utterance that unlocks speech in a click. */
    spoken: () => all.filter((utterance) => utterance.text !== ''),
  }
}

export function fakeAudio() {
  const plays: string[] = []
  let refuse = false
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (this: HTMLMediaElement) {
    if (this.src.startsWith('data:')) return Promise.resolve() // the unlocking silence
    plays.push(this.src)
    if (refuse) return Promise.reject(new DOMException('play() needs a user gesture', 'NotAllowedError'))
    return Promise.resolve()
  })
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})

  let made = 0
  const revoked: string[] = []
  // Audio clips only: the candidate's PDF gets its own object URL (M10 N4), counted apart.
  const created = vi.fn(() => `blob:clip-${++made}`)
  const createObjectURL = (object: Blob) => (object instanceof File ? `blob:document-${object.name}` : created())
  Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', {
    value: (url: string) => {
      if (!url.startsWith('blob:document')) revoked.push(url)
    },
    configurable: true,
  })

  return {
    /** Every clip play() was called for, in order. */
    plays,
    created,
    revoked,
    /** The next play() calls reject as a browser blocking autoplay does. */
    refuseAutoplay: (on: boolean) => {
      refuse = on
    },
    /** Fires a media event (playing, ended, error) on the element that last played. */
    fire: (type: 'playing' | 'ended' | 'error') => {
      const element = play.mock.contexts.at(-1) // `this` of the last play() call
      if (!(element instanceof HTMLMediaElement)) throw new Error('nothing has played')
      element.dispatchEvent(new Event(type))
    },
  }
}

export function restoreObjectUrls() {
  Reflect.deleteProperty(URL, 'createObjectURL')
  Reflect.deleteProperty(URL, 'revokeObjectURL')
}

// "RIFF" + 8 bytes: what /tts sends is a WAV, and only its type matters here.
export const WAV = new Uint8Array([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x41, 0x56, 0x45])
