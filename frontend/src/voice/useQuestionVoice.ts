// The current question's voice (M3b E7, E8, E10, E11). With panel voices, the clip is
// requested as soon as the question arrives and plays when it comes; with the browser
// voice, it speaks at once. Anything that goes wrong with a clip falls back to the
// browser voice for that turn, silently. The text is always on screen regardless.
import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchTts } from '../api/client'
import type { QuestionOut } from '../api/types'
import type { VoiceChoice } from '../state/flow'
import type { Player } from './player'

/**
 * off: no voice chosen. loading: the clip hasn't arrived. idle: nothing playing, Replay
 * works. speaking: playing now. blocked: the browser wants a click first (E10).
 */
export type VoiceStatus = 'off' | 'loading' | 'idle' | 'speaking' | 'blocked'

export type QuestionVoice = {
  status: VoiceStatus
  /** Plays the question again, from the cached clip or the browser voice. Call from a click. */
  replay: () => void
}

type Args = {
  player: Player
  choice: VoiceChoice | null
  sessionId: string
  question: QuestionOut
  submitting: boolean
}

export function useQuestionVoice({ player, choice, sessionId, question, submitting }: Args): QuestionVoice {
  const { turn, question: text } = question
  const archetypeKey = question.panelist.archetype_key
  const voiced = choice === 'panel' || choice === 'browser'
  // Status reports carry their turn, so a new question starts fresh without an effect
  // resetting it, and a late report from the last question is ignored.
  const [reported, setReported] = useState<{ turn: number; status: VoiceStatus }>({ turn: 0, status: 'off' })
  const submitted = useRef(submitting)
  const say = useRef<(() => void) | null>(null)

  useEffect(() => {
    submitted.current = submitting
    if (submitting) player.stop() // the panel listens to the answer, not to itself
  }, [submitting, player])

  useEffect(() => {
    if (!voiced) return
    const controller = new AbortController()
    let clip: string | null = null // this turn's audio as an object URL, kept for Replay
    let useBrowser = choice === 'browser'
    const report = (status: VoiceStatus) => setReported({ turn, status })
    const events = {
      onStart: () => report('speaking'),
      onEnd: () => report('idle'),
      onBlocked: () => report('blocked'),
    }
    const speak = () => {
      if (useBrowser || clip === null) {
        player.speak(text, archetypeKey, events)
        return
      }
      player.playClip(clip, {
        ...events,
        onFailed: () => {
          useBrowser = true // the browser couldn't play the clip: its own voice instead (E8)
          speak()
        },
      })
    }
    say.current = speak

    if (useBrowser) {
      if (!submitted.current) speak()
    } else {
      void fetchTts(sessionId, turn, controller.signal).then((result) => {
        if (controller.signal.aborted) return // the turn moved on, or the screen closed
        if (submitted.current) {
          report('idle') // E7: the answer went first, so this clip is discarded
          return
        }
        if (result.ok) clip = URL.createObjectURL(result.data)
        else useBrowser = true // E8: any /tts failure, the browser voice for this turn
        report('idle')
        speak()
      })
    }

    return () => {
      controller.abort()
      say.current = null
      player.stop()
      if (clip !== null) URL.revokeObjectURL(clip) // an object URL holds its Blob until revoked
    }
  }, [voiced, choice, sessionId, turn, text, archetypeKey, player])

  const replay = useCallback(() => say.current?.(), [])

  let status: VoiceStatus = 'off'
  if (voiced) status = reported.turn === turn ? reported.status : choice === 'panel' ? 'loading' : 'idle'
  if (submitting && status !== 'off') status = 'idle'
  return { status, replay }
}
