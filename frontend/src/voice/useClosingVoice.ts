// M15: on a hand-off, the outgoing panelist's closing line is spoken in its own voice
// before the next question. Panel voices ask /tts for the closing line said after the
// answered turn (the server still speaks only what it generated, never text sent to it);
// any failure there falls back to the browser voice, as questions do (M3b E8). `onDone`
// fires once, when the line has been said, or couldn't be.
import { useEffect, useRef } from 'react'
import { fetchTts } from '../api/client'
import type { ClosingOut } from '../api/types'
import type { VoiceChoice } from '../state/flow'
import type { Player } from './player'

type Args = {
  player: Player
  choice: VoiceChoice | null
  sessionId: string
  /** The answered turn the closing line followed: the next question's turn less one. */
  turn: number
  closing: ClosingOut | null
  /** True while the closing line is on screen; voice starts when it turns true. */
  active: boolean
  /** The recorded demo has no server: the browser voice only. */
  offline?: boolean
  onDone: () => void
}

export function closingVoiced(choice: VoiceChoice | null): boolean {
  return choice === 'panel' || choice === 'browser'
}

export function useClosingVoice({ player, choice, sessionId, turn, closing, active, offline = false, onDone }: Args): void {
  const done = useRef(onDone)
  useEffect(() => {
    done.current = onDone
  }, [onDone])

  const line = closing?.line ?? null
  const archetypeKey = closing?.panelist.archetype_key ?? null

  useEffect(() => {
    if (!active || line === null || archetypeKey === null || !closingVoiced(choice)) return
    const controller = new AbortController()
    let finished = false
    let url: string | null = null
    const finish = () => {
      if (finished) return
      finished = true
      done.current()
    }
    const events = { onStart: () => {}, onEnd: finish, onBlocked: finish }
    const browser = () => {
      if (!controller.signal.aborted) player.speak(line, archetypeKey, events)
    }
    if (choice === 'browser' || offline) {
      browser()
    } else {
      void fetchTts(sessionId, turn, null, controller.signal, true).then((result) => {
        if (controller.signal.aborted) return
        if (!result.ok) {
          browser()
          return
        }
        url = URL.createObjectURL(result.data.audio)
        player.playClip(url, { ...events, onFailed: browser })
      })
    }
    return () => {
      finished = true // a stop from here isn't the line ending
      controller.abort()
      player.stop()
      if (url !== null) URL.revokeObjectURL(url)
    }
  }, [active, line, archetypeKey, choice, offline, sessionId, turn, player])
}
