// GSAP choreography (M10 N13), only where motion shows a change of state: the handoff
// between panelists, the preparing screen's seats arriving, the source reveal, and the
// recorded demo's steps. Hover and focus stay plain CSS transitions. Every timeline is
// built inside gsap.matchMedia() for "no-preference", so with prefers-reduced-motion no
// GSAP animation runs and everything is already in its final state. useGSAP reverts it
// all on unmount or when its dependencies change.
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import type { RefObject } from 'react'

gsap.registerPlugin(useGSAP)

/** Motion runs only when this matches; with reduced motion, nothing is animated. */
export const MOTION_OK = '(prefers-reduced-motion: no-preference)'

/**
 * The handoff, one timeline per question: the lit card comes up (the card that was lit
 * has already dimmed through its CSS colour transition), then the question, then the
 * previous answer's source slides in. Targets are found by data attribute inside `scope`.
 */
export function useHandoff(scope: RefObject<HTMLElement | null>, turn: number): void {
  useGSAP(
    () => {
      const mm = gsap.matchMedia()
      mm.add(MOTION_OK, () => {
        const root = scope.current
        if (root === null) return
        const lit = root.querySelector('[data-lit]')
        const question = root.querySelector('[data-question]')
        const reveal = root.querySelector('[data-reveal]')
        const timeline = gsap.timeline({ defaults: { ease: 'power2.out' } })
        if (lit) timeline.from(lit, { autoAlpha: 0.4, scale: 0.97, duration: 0.35 })
        if (question) timeline.from(question, { autoAlpha: 0, y: 12, duration: 0.4 }, lit ? '>-0.1' : 0)
        if (reveal) timeline.from(reveal, { autoAlpha: 0, x: 24, duration: 0.45 }, '<0.1')
      })
    },
    { scope, dependencies: [turn], revertOnUpdate: true },
  )
}

/** The preparing screen's seats arrive one after another (N6), first seat first. */
export function useSeatsArrive(scope: RefObject<HTMLElement | null>, count: number): void {
  useGSAP(
    () => {
      const mm = gsap.matchMedia()
      mm.add(MOTION_OK, () => {
        const seats = scope.current?.querySelectorAll('[data-seat]')
        if (seats && seats.length > 0) gsap.from(seats, { autoAlpha: 0, y: 8, duration: 0.35, stagger: 0.08, ease: 'power2.out' })
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
        if (scope.current) gsap.from(scope.current, { autoAlpha: 0, y: 8, duration: 0.3, ease: 'power2.out' })
      })
    },
    { scope, dependencies: [step], revertOnUpdate: true },
  )
}
