// One page-wide player for the panel's voice: a single HTMLAudioElement for MiMo clips
// and the browser's speechSynthesis for the fallback. One of each, so a new question's
// voice always stops the last one, and so a click can unlock them for later (E10).
import { browserSpeech, type SpeechEvents, speakQuestion } from './browserVoice'

export type PlaybackEvents = SpeechEvents

export type Player = {
  /** Call inside a click. Browsers let a page play sound later once a click has started some. */
  unlock: () => void
  /** Plays a clip; `onFailed` when the browser can't play it at all (the voice falls back, E8). */
  playClip: (url: string, events: PlaybackEvents & { onFailed: () => void }) => void
  speak: (text: string, archetypeKey: string, events: PlaybackEvents) => void
  /** Stops whatever is playing; its onEnd fires. */
  stop: () => void
}

// A WAV with no samples: something to play inside the unlocking click.
const SILENCE = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA='

export function createPlayer(): Player {
  let audio: HTMLAudioElement | null = null
  let active: PlaybackEvents | null = null // the playback now under way
  let stopSpeech: (() => void) | null = null

  const element = () => (audio ??= new Audio())

  /** Ends `events`' playback, unless another has replaced it since (a late event from an old one). */
  const finish = (events: PlaybackEvents) => {
    if (active !== events) return
    active = null
    events.onEnd()
  }

  function stop() {
    if (audio !== null) {
      audio.onplaying = null
      audio.onended = null
      audio.onerror = null
      audio.pause()
      audio.removeAttribute('src')
    }
    stopSpeech?.()
    stopSpeech = null
    if (active !== null) finish(active)
  }

  return {
    unlock() {
      // Best effort: nothing here may stop the click that called it from starting the defense.
      try {
        const a = element()
        if (a.getAttribute('src') === null) {
          a.muted = true
          a.src = SILENCE
          // play() is called here, inside the click; only its result is waited on.
          Promise.resolve(a.play()).then(
            () => {
              if (a.src === SILENCE) a.pause()
              a.muted = false
            },
            () => {
              a.muted = false
            },
          )
        }
        // Safari speaks later only if something was spoken inside a click first.
        const synth = browserSpeech()
        if (synth !== null && !synth.speaking) synth.speak(new SpeechSynthesisUtterance(''))
      } catch {
        // No audio unlocked: a later blocked play shows the play button instead (E10).
      }
    },

    playClip(url, events) {
      stop()
      const a = element()
      active = events
      a.muted = false
      a.onplaying = () => events.onStart()
      a.onended = () => finish(events)
      a.onerror = () => {
        if (active !== events) return
        active = null
        events.onFailed()
      }
      a.src = url
      Promise.resolve(a.play()).catch((error: unknown) => {
        // AbortError: stopped before it started. Anything else also fires `error` above.
        if (active === events && error instanceof DOMException && error.name === 'NotAllowedError') events.onBlocked()
      })
    },

    speak(text, archetypeKey, events) {
      stop()
      active = events
      stopSpeech = speakQuestion(text, archetypeKey, {
        onStart: events.onStart,
        onEnd: () => finish(events),
        onBlocked: events.onBlocked,
      })
    },

    stop,
  }
}
