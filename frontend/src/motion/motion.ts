// GSAP choreography (M10 N13), only where motion shows a change of state: the handoff
// between panelists, the preparing screen's seats arriving, the source reveal, and the
// recorded demo's steps. Hover and focus stay plain CSS transitions. Every timeline is
// built inside gsap.matchMedia() for "no-preference", so with prefers-reduced-motion no
// GSAP animation runs and everything is already in its final state. useGSAP reverts it
// all on unmount or when its dependencies change.
//
// Every tween is a fromTo with its end state spelled out. A from() takes the element's
// current look as its end, and autoAlpha reads a hidden element as 0: inside the demo
// replay, whose step-in hides the whole screen for a frame, the lit card took that as
// its end state and finished invisible (M11.1).
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import type { RefObject } from 'react'

gsap.registerPlugin(useGSAP)

/** Motion runs only when this matches; with reduced motion, nothing is animated. */
export const MOTION_OK = '(prefers-reduced-motion: no-preference)'

/** Where every tween ends: fully shown, in place. */
const SHOWN = { autoAlpha: 1, x: 0, y: 0, scale: 1 }

/**
 * The handoff, one timeline per stage: the lit card comes up (the card that was lit
 * has already dimmed through its CSS colour transition), then the question, or the
 * outgoing panelist's closing line (M15), then the previous answer's source slides in.
 * `stage` changes once per question, and once more when a closing line gives way to the
 * question; `revealIn` false skips the source, which came in with the closing line.
 * Targets are found by data attribute inside `scope`.
 */
export function useHandoff(scope: RefObject<HTMLElement | null>, stage: number | string, revealIn = true): void {
  useGSAP(
    () => {
      const mm = gsap.matchMedia()
      mm.add(MOTION_OK, () => {
        const root = scope.current
        if (root === null) return
        const lit = root.querySelector('[data-lit]')
        const question = root.querySelector('[data-closing], [data-question]')
        const reveal = revealIn ? root.querySelector('[data-reveal]') : null
        const timeline = gsap.timeline({ defaults: { ease: 'power2.out' } })
        if (lit) timeline.fromTo(lit, { autoAlpha: 0.4, scale: 0.97 }, { ...SHOWN, duration: 0.35 })
        if (question) timeline.fromTo(question, { autoAlpha: 0, y: 12 }, { ...SHOWN, duration: 0.4 }, lit ? '>-0.1' : 0)
        if (reveal) timeline.fromTo(reveal, { autoAlpha: 0, x: 24 }, { ...SHOWN, duration: 0.45 }, '<0.1')
      })
    },
    { scope, dependencies: [stage], revertOnUpdate: true },
  )
}

/** The preparing screen's seats arrive one after another (N6), first seat first. */
export function useSeatsArrive(scope: RefObject<HTMLElement | null>, count: number): void {
  useGSAP(
    () => {
      const mm = gsap.matchMedia()
      mm.add(MOTION_OK, () => {
        const seats = scope.current?.querySelectorAll('[data-seat]')
        if (seats && seats.length > 0) gsap.fromTo(seats, { autoAlpha: 0, y: 8 }, { ...SHOWN, duration: 0.35, stagger: 0.08, ease: 'power2.out' })
      })
    },
    { scope, dependencies: [count], revertOnUpdate: true },
  )
}

/** One step of the recorded demo replaying (N5): the next screen settles in. */
export function useStepIn(scope: RefObject<HTMLElement | null>, step: number): void {
  useGSAP(
    () => {
      const mm = gsap.matchMedia()
      mm.add(MOTION_OK, () => {
        if (scope.current) gsap.fromTo(scope.current, { autoAlpha: 0, y: 8 }, { ...SHOWN, duration: 0.3, ease: 'power2.out' })
      })
    },
    { scope, dependencies: [step], revertOnUpdate: true },
  )
}
