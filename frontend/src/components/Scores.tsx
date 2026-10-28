// Scores arrive on v1's 1-5 scale and show on 100 (D17, v1.2.1), always with the
// caption that says what the 100 means. Shown only in the report (M8 K7).
import type { Evaluation } from '../api/types'
import { SCALE_CAPTION, toHundred, turnOverall } from '../lib/scoreDisplay'

export function ScoreRow({ clarity, depth, grounding, overall }: { clarity: number; depth: number; grounding: number; overall?: number }) {
  const cells: [string, number][] = [
    ['Clarity', clarity],
    ['Depth', depth],
    ['Grounding', grounding],
  ]
  return (
    <dl className="grid grid-cols-3 gap-2 text-center sm:grid-cols-4">
      {overall !== undefined && (
        <div className="col-span-3 rounded-xl border border-accent bg-accent-soft p-2 sm:col-span-1">
          <dt className="font-mono text-xs text-accent">Overall</dt>
          <dd className="font-serif text-xl font-medium text-ink">{toHundred(overall)}/100</dd>
        </div>
      )}
      {cells.map(([name, value]) => (
        <div key={name} className="rounded-xl border border-line bg-field p-2">
          <dt className="font-mono text-xs text-muted">{name}</dt>
          <dd className="font-serif text-xl font-medium">{toHundred(value)}/100</dd>
        </div>
      ))}
    </dl>
  )
}

export function ScaleCaption() {
  return <p className="text-xs text-muted">{SCALE_CAPTION}</p>
}

export function EvaluationScores({ evaluation }: { evaluation: Evaluation }) {
  return (
    <div className="space-y-2">
      <ScoreRow
        clarity={evaluation.clarity}
        depth={evaluation.depth}
        grounding={evaluation.grounding}
        overall={evaluation.total / 3}
      />
      <p className="sr-only">Overall {turnOverall(evaluation.total)} out of 100</p>
    </div>
  )
}
