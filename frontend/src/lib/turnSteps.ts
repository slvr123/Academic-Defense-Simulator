// The turn-progress steps above the question (M8 K6), driven by the real phase:
// the panelist asks (the question is still being written out or voiced), the candidate
// answers, the panel reviews (the answer is being scored), and the turn hands off when
// the next question arrives. Handoff is never a waiting state of its own: the API
// returns the score and the next question together, so a turn is handed off the moment
// the screen moves on.
export const TURN_STEPS = ['Asking', 'Your answer', 'Reviewing', 'Handoff'] as const

export type TurnStep = (typeof TURN_STEPS)[number]

export function currentStep({ submitting, asking }: { submitting: boolean; asking: boolean }): TurnStep {
  if (submitting) return 'Reviewing'
  return asking ? 'Asking' : 'Your answer'
}
