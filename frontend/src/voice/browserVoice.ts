// The browser's own speech (speechSynthesis), ported unchanged from v1's voice
// component (streamlit_app.py, _voice_component_html; M3b E12): the same voice pick,
// the same pitch and rate in every branch, and the same keep-alive. The archetype ->
// voice map is v1's VOICE_PROFILES, copied as JSON; a backend test fails on drift.
import profiles from './voiceProfiles.json'

export type VoiceProfile = { voice_prefs: string[]; pitch: number; rate: number }

export type SpeechEvents = {
  onStart: () => void
  onEnd: () => void
  onBlocked: () => void // the browser refused to speak without a click (E10)
}

const VOICE_PROFILES: Record<string, VoiceProfile> = profiles

// Chrome silently truncates long network-voice utterances after ~15 s unless resumed.
export const VOICE_KEEPALIVE_MS = 8000
// Some engines never fire `voiceschanged`; speak with the default voice after this.
export const VOICES_WAIT_MS = 1200

export function browserSpeech(): SpeechSynthesis | null {
  return typeof window.speechSynthesis === 'undefined' ? null : window.speechSynthesis
}

/** v1 Decision 4 steps 2 and 3: the first preferred name the browser has wins; none is null. */
export function pickVoice(synth: SpeechSynthesis, prefs: readonly string[]): SpeechSynthesisVoice | null {
  const voices = synth.getVoices() || []
  for (const pref of prefs) {
    for (const voice of voices) {
      if (voice.name === pref) return voice
    }
  }
  return null
}

/** v1 Decision 4 step 1: getVoices() is often empty at first; wait for `voiceschanged`. */
function whenVoicesReady(synth: SpeechSynthesis, callback: () => void): void {
  if ((synth.getVoices() || []).length > 0) {
    callback()
    return
  }
  let fired = false
  const fire = () => {
    if (fired) return
    fired = true
    callback()
  }
  synth.addEventListener('voiceschanged', fire, { once: true })
  setTimeout(fire, VOICES_WAIT_MS)
}

/**
 * Speaks one question in its archetype's voice. Returns a function that stops it. With
 * no speechSynthesis at all, ends at once: the text is on screen, so nothing is lost.
 */
export function speakQuestion(text: string, archetypeKey: string, events: SpeechEvents): () => void {
  const synth = browserSpeech()
  if (synth === null) {
    events.onEnd()
    return () => {}
  }
  let cancelled = false
  let keepalive: ReturnType<typeof setInterval> | null = null
  const stopKeepalive = () => {
    if (keepalive !== null) clearInterval(keepalive)
    keepalive = null
  }

  whenVoicesReady(synth, () => {
    if (cancelled) return
    synth.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    const profile = Object.hasOwn(VOICE_PROFILES, archetypeKey) ? VOICE_PROFILES[archetypeKey] : undefined
    const voice = profile ? pickVoice(synth, profile.voice_prefs) : null
    if (voice) utterance.voice = voice
    // v1 Decision 4 step 4, the load-bearing one: pitch and rate in EVERY branch, the
    // default-voice branch included, so a panel never collapses into one voice.
    utterance.pitch = profile?.pitch ?? 1
    utterance.rate = profile?.rate ?? 1
    utterance.onstart = events.onStart
    utterance.onend = () => {
      stopKeepalive()
      events.onEnd()
    }
    utterance.onerror = (event) => {
      stopKeepalive()
      if (event.error === 'not-allowed') events.onBlocked()
      else if (event.error !== 'interrupted' && event.error !== 'canceled') events.onEnd()
    }
    synth.speak(utterance)
    stopKeepalive()
    keepalive = setInterval(() => {
      if (synth.speaking) synth.resume()
      else stopKeepalive()
    }, VOICE_KEEPALIVE_MS)
  })

  return () => {
    cancelled = true
    stopKeepalive()
    synth.cancel()
  }
}
