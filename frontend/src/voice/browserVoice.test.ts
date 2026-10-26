// v1's browser-voice behaviour, ported (M3b E12): the preference order, pitch and rate in
// every branch, waiting for the voice list, and the keep-alive.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fakeSpeech } from '../test/voiceFakes'
import { pickVoice, speakQuestion, VOICE_KEEPALIVE_MS, VOICES_WAIT_MS } from './browserVoice'

const events = () => ({ onStart: vi.fn(), onEnd: vi.fn(), onBlocked: vi.fn() })

afterEach(() => {
  vi.useRealTimers()
})

describe('pickVoice', () => {
  it('takes the first preferred name the browser has, not the browser’s order', () => {
    const { synth } = fakeSpeech(['Google US English', 'Microsoft Zira - English (United States)'])
    const prefs = ['Missing', 'Microsoft Zira - English (United States)', 'Google US English']
    expect(pickVoice(synth as unknown as SpeechSynthesis, prefs)?.name).toBe('Microsoft Zira - English (United States)')
  })

  it('returns null when none is present', () => {
    const { synth } = fakeSpeech(['Google US English'])
    expect(pickVoice(synth as unknown as SpeechSynthesis, ['Missing'])).toBeNull()
  })
})

describe('speakQuestion', () => {
  it('applies the archetype’s pitch and rate even when no preferred voice exists', () => {
    const speech = fakeSpeech(['Some Other Voice'])
    speakQuestion('Why thirty?', 'devils_advocate', events())
    expect(speech.spoken()).toMatchObject([{ text: 'Why thirty?', voice: null, pitch: 0.95, rate: 1.1 }])
  })

  it('waits for the voice list before speaking, then speaks anyway after 1.2 s', () => {
    vi.useFakeTimers()
    const speech = fakeSpeech([])
    speakQuestion('Why thirty?', 'methodology_expert', events())
    expect(speech.spoken()).toEqual([])
    vi.advanceTimersByTime(VOICES_WAIT_MS)
    expect(speech.spoken().map((u) => u.text)).toEqual(['Why thirty?'])
  })

  it('nudges a long utterance every 8 s while it is still speaking, as Chrome needs', () => {
    vi.useFakeTimers()
    const speech = fakeSpeech()
    speakQuestion('A long question.', 'methodology_expert', events())
    speech.synth.speaking = true
    vi.advanceTimersByTime(VOICE_KEEPALIVE_MS)
    expect(speech.synth.resume).toHaveBeenCalledTimes(1)
  })

  it('reports a refusal as blocked, and a cancel as nothing', () => {
    const speech = fakeSpeech()
    const handlers = events()
    speakQuestion('Why thirty?', 'methodology_expert', handlers)
    const [utterance] = speech.spoken()
    utterance.onerror?.({ error: 'interrupted' })
    expect(handlers.onEnd).not.toHaveBeenCalled()
    utterance.onerror?.({ error: 'not-allowed' })
    expect(handlers.onBlocked).toHaveBeenCalledTimes(1)
  })

  it('ends at once in a browser without speechSynthesis', () => {
    const handlers = events()
    speakQuestion('Why thirty?', 'methodology_expert', handlers)
    expect(handlers.onEnd).toHaveBeenCalledTimes(1)
  })
})
