// The debrief (M8 K8): the headline score, the three dimension averages, then one card
// per question with the answer, the passage, the gap the panelist found and a stronger
// answer. Every score of the defense is here and nowhere earlier (K7). v1's other
// sections (narrative, per panelist, pushback) and the full transcript stay.
import { type ReactNode, useEffect } from 'react'
import type { AnswerSuggestion, DefenseReport, PanelOptions, ReportResponse, TranscriptTurn } from '../api/types'
import { PUSHBACK_OUTCOME_LABELS, panelistName } from '../lib/labels'
import { SCALE_CAPTION, toHundred } from '../lib/scoreDisplay'
import { Avatar } from './Avatar'
import { EvaluationScores, ScaleCaption, ScoreRow } from './Scores'
import { SourcePassage } from './Sources'
import { Button, Eyebrow } from './ui'

type Seat = { title: string; icon: string }

/** Titles and icons by archetype: from the transcript, or the rosters for a panelist who never asked. */
function seats(transcript: TranscriptTurn[], options: PanelOptions | null): Map<string, Seat> {
  const map = new Map<string, Seat>()
  const everyOption = options ? [...options.rosters.flatMap((r) => r.archetypes), options.devils_advocate] : []
  for (const option of everyOption) map.set(option.archetype_key, { title: option.title, icon: option.icon })
  for (const turn of transcript) map.set(turn.panelist.archetype_key, { title: turn.panelist.title, icon: turn.panelist.icon })
  return map
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-4 rounded-2xl border border-line-strong bg-card p-5 sm:p-8">
      <Eyebrow className="text-muted">{title}</Eyebrow>
      {children}
    </section>
  )
}

function Headline({ report, overallRaw }: { report: DefenseReport; overallRaw: number }) {
  return (
    <section aria-label="Overall" className="space-y-5 rounded-2xl border border-accent bg-card p-5 sm:p-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <Eyebrow className="text-accent">Overall</Eyebrow>
          <p className="font-serif text-6xl leading-none font-medium sm:text-7xl">
            {toHundred(overallRaw)}
            <span className="text-2xl text-muted sm:text-3xl">/100</span>
          </p>
        </div>
        <p className="max-w-sm text-sm text-muted">The mean of your clarity, depth and grounding across every question.</p>
      </div>
      <ScoreRow clarity={report.overall_avg_clarity} depth={report.overall_avg_depth} grounding={report.overall_avg_grounding} />
      <p className="text-xs text-muted">{SCALE_CAPTION}</p>
    </section>
  )
}

function Labelled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <p className="font-mono text-xs text-subtle">{label}</p>
      {children}
    </div>
  )
}

function QuestionCard({ turn, suggestion, open }: { turn: TranscriptTurn; suggestion: AnswerSuggestion | undefined; open: boolean }) {
  const gap = turn.evaluation.primary_gap
  return (
    <details open={open} className="group rounded-2xl border border-line-strong bg-card print:break-inside-avoid-page">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-3 p-4 sm:px-6 [&::-webkit-details-marker]:hidden">
        <Avatar icon={turn.panelist.icon} size="sm" />
        <span className="min-w-0 flex-1">
          <span className="block font-mono text-xs text-muted">
            Question {turn.turn} · {panelistName(turn.panelist.name)} · {turn.panelist.title}
          </span>
          <span className="block truncate font-serif text-base group-open:whitespace-normal">{turn.question}</span>
        </span>
        <span aria-hidden className="shrink-0 text-muted transition-transform group-open:rotate-180 motion-reduce:transition-none print:hidden">
          ▾
        </span>
      </summary>
      <div className="space-y-5 border-t border-line p-4 sm:p-6">
        <EvaluationScores evaluation={turn.evaluation} />
        <Labelled label="Your answer">
          <p className="leading-relaxed whitespace-pre-line text-ink-dim">{turn.answer}</p>
        </Labelled>
        <Labelled label="The passage">
          {turn.sources.map((source, i) => (
            <SourcePassage key={i} source={source} />
          ))}
        </Labelled>
        <Labelled label="The gap the panelist found">
          <p className="leading-relaxed">{gap ?? 'No gap noted for this answer.'}</p>
        </Labelled>
        <Labelled label="A stronger answer">
          <p className="leading-relaxed">{suggestion?.suggestion ?? 'No suggestion for this answer.'}</p>
        </Labelled>
      </div>
    </details>
  )
}

function Transcript({ transcript }: { transcript: TranscriptTurn[] }) {
  return (
    <Section title="Full transcript">
      <ol className="space-y-6">
        {transcript.map((turn) => (
          <li key={turn.turn} className="space-y-2">
            <p className="font-mono text-xs text-muted">
              {panelistName(turn.panelist.name)} · {turn.panelist.title} · question {turn.turn}, difficulty {turn.difficulty}
            </p>
            <p className="font-serif leading-relaxed">{turn.question}</p>
            <blockquote className="border-l-2 border-line-strong pl-3 text-sm whitespace-pre-line text-ink-dim">{turn.answer}</blockquote>
          </li>
        ))}
      </ol>
    </Section>
  )
}

/** M10 N11: printing expands every question card, whether it's "Save as PDF" or the
 * browser's own Print, and puts each card back as it was afterwards. */
function useExpandedForPrint() {
  useEffect(() => {
    let reopen: HTMLDetailsElement[] = []
    const before = () => {
      reopen = Array.from(document.querySelectorAll<HTMLDetailsElement>('[data-report] details:not([open])'))
      for (const details of reopen) details.open = true
    }
    const after = () => {
      for (const details of reopen) details.open = false
      reopen = []
    }
    window.addEventListener('beforeprint', before)
    window.addEventListener('afterprint', after)
    return () => {
      window.removeEventListener('beforeprint', before)
      window.removeEventListener('afterprint', after)
    }
  }, [])
}

type ReportScreenProps = { result: ReportResponse; options: PanelOptions | null; onRestart: () => void; restartLabel?: string }

export function ReportScreen({ result, options, onRestart, restartLabel = 'Start a new defense' }: ReportScreenProps) {
  const { report, transcript } = result
  const byKey = seats(transcript, options)
  const suggestions = report.suggestions_fallback_used ? [] : (report.answer_suggestions ?? [])
  useExpandedForPrint()
  return (
    <div data-report className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="space-y-1">
          <h2 className="font-serif text-3xl font-medium">Your debrief</h2>
          <p className="font-mono text-xs text-muted">
            {result.topic} · {result.domain}
          </p>
        </div>
        <Button variant="secondary" className="print:hidden" onClick={() => window.print()}>
          Save as PDF
        </Button>
      </div>

      <Headline report={report} overallRaw={result.overall_raw} />

      <Section title="The panel's view">
        <p className="font-serif text-lg leading-relaxed">
          {report.narrative_fallback_used || !report.narrative ? 'Narrative unavailable this session' : report.narrative}
        </p>
        <p className="text-sm text-muted">
          <span className="font-medium text-ink">Difficulty by turn: </span>
          {report.difficulty_trajectory.join(' → ')}
        </p>
      </Section>

      <section aria-label="Question by question" className="space-y-3">
        <Eyebrow className="text-muted">Question by question</Eyebrow>
        {report.suggestions_fallback_used && <p className="text-sm text-muted">Suggestions unavailable this session</p>}
        {transcript.map((turn, i) => (
          <QuestionCard key={turn.turn} turn={turn} suggestion={suggestions.find((s) => s.turn_index === turn.turn - 1)} open={i === 0} />
        ))}
      </section>

      <Section title="Per panelist">
        <ul className="space-y-5">
          {report.panelist_sections.map((section) => {
            const seat = byKey.get(section.archetype_key)
            return (
              <li key={section.archetype_key} className="space-y-2">
                <div className="flex items-center gap-3">
                  {seat && <Avatar icon={seat.icon} size="sm" />}
                  <p className="text-sm">
                    <span className="font-medium">{panelistName(section.panelist_name)}</span>
                    {seat && <span className="text-muted"> · {seat.title}</span>}
                    <span className="text-muted"> · {section.turns_taken} turn(s)</span>
                  </p>
                </div>
                {section.turns_taken > 0 && (
                  <>
                    <ScoreRow clarity={section.avg_clarity} depth={section.avg_depth} grounding={section.avg_grounding} />
                    <ul className="list-disc pl-5 text-sm">
                      {section.primary_gaps.map((gap, i) => (
                        <li key={i}>{gap}</li>
                      ))}
                    </ul>
                  </>
                )}
              </li>
            )
          })}
        </ul>
        <ScaleCaption />
      </Section>

      <Section title="Pushback events">
        {report.pushback_events.length === 0 ? (
          <p className="text-sm">No escalation moments this session</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {report.pushback_events.map((event) => (
              <li key={event.turn_index}>
                Turn {event.turn_index + 1}: difficulty {event.difficulty_from} → {event.difficulty_to},{' '}
                {PUSHBACK_OUTCOME_LABELS[event.outcome]}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Transcript transcript={transcript} />

      <Button className="print:hidden" onClick={onRestart}>
        {restartLabel}
      </Button>
    </div>
  )
}
