// v1.2.1 Decision 4, ported from academic_defense_simulator/report.py
// (rescale_score_to_100): a 1-5 score shown on a 100-point scale, (x / 5) * 100,
// rounded half up. Display only; the API's scores stay 1-5.

export const SCALE_CAPTION =
  'Panel ratings are a 1–5 judgment rescaled to a 100-point display, not a percentage of correct answers. The scale floor is 20.'

/** Math.floor(x + 0.5), as v1 does, so a tie rounds up (20.5 -> 21). */
export function toHundred(score: number): number {
  return Math.floor((score / 5) * 100 + 0.5)
}

/** One turn's three criteria, averaged on 1-5 first and rescaled after, as v1.2.1 requires. */
export function turnOverall(total: number): number {
  return toHundred(total / 3)
}
