// The current question's voice (M3b E7, E8, E10, E11). With panel voices, the clip is
// requested as soon as the question arrives and plays when it comes; with the browser
// voice, it speaks at once. Anything that goes wrong with the first clip falls back to
// the browser voice for that turn, silently. The text is always on screen regardless.
// M6 H9: the clip comes in parts (one, while the server's split flag is off); each part
// plays in turn, and the next one is fetched while the current one plays.
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
    let first: string | null = null // part 0 as an object URL; it and the rest are kept for Replay
    let parts = 1 // how many parts this question has, known once part 0 arrives
    const clips = new Map<number, Promise<string | null>>() // part -> object URL, or null if it failed
    const made: string[] = [] // every object URL this turn created, revoked at cleanup
    let useBrowser = choice === 'browser'
    const report = (status: VoiceStatus) => setReported({ turn, status })
    const events = {
      onStart: () => report('speaking'),
      onEnd: () => report('idle'),
      onBlocked: () => report('blocked'),
    }
    const fetchPart = (part: number): Promise<string | null> => {
      const cached = clips.get(part)
      if (cached !== undefined) return cached
      const pending = fetchTts(sessionId, turn, part, controller.signal).then((result) => {
        // E7: audio that arrives after the answer went is discarded before it becomes a URL.
        if (controller.signal.aborted || submitted.current || !result.ok) return null
        if (part === 0) parts = result.data.parts
        const url = URL.createObjectURL(result.data.audio)
        made.push(url)
        return url
      })
      clips.set(part, pending)
      return pending
    }
    const moved = () => controller.signal.aborted || submitted.current
    const playPart = (part: number, url: string) => {
      if (part + 1 < parts) void fetchPart(part + 1) // fetched while this part plays
      player.playClip(url, {
        onStart: events.onStart,
        onBlocked: events.onBlocked,
        onEnd: () => {
          if (moved() || part + 1 >= parts) {
            events.onEnd()
            return
          }
          void fetchPart(part + 1).then((next) => {
            if (moved()) return
            if (next === null) events.onEnd() // a later part failed: stop there; the text is on screen
            else playPart(part + 1, next)
          })
        },
        onFailed: () => {
          if (part > 0) {
            events.onEnd()
            return
          }
          useBrowser = true // the browser couldn't play the clip: its own voice instead (E8)
          speak()
        },
      })
    }
    const speak = () => {
      if (useBrowser || first === null) {
        player.speak(text, archetypeKey, events)
        return
      }
      playPart(0, first)
    }
    say.current = speak

    if (useBrowser) {
      if (!submitted.current) speak()
    } else {
      void fetchPart(0).then((url) => {
        if (controller.signal.aborted) return // the turn moved on, or the screen closed
        if (submitted.current) {
          report('idle') // E7: the answer went first, so this clip is discarded
          return
        }
        if (url !== null) first = url
        else useBrowser = true // E8: any /tts failure, the browser voice for this turn
        report('idle')
        speak()
      })
    }

    return () => {
      controller.abort()
      say.current = null
      player.stop()
      for (const url of made) URL.revokeObjectURL(url) // an object URL holds its Blob until revoked
    }
  }, [voiced, choice, sessionId, turn, text, archetypeKey, player])

  const replay = useCallback(() => say.current?.(), [])

  let status: VoiceStatus = 'off'
  if (voiced) status = reported.turn === turn ? reported.status : choice === 'panel' ? 'loading' : 'idle'
  if (submitting && status !== 'off') status = 'idle'
  return { status, replay }
}
