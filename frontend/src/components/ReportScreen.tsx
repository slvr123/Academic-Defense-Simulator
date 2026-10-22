// The basic report (D13): v1's report sections, as v1 renders them, plus the full
// transcript with each turn's sources. The debrief-style rebuild comes later.
import type { ReactNode } from 'react'
import type { DefenseReport, PanelOptions, ReportResponse, TranscriptTurn } from '../api/types'
import { accentFor } from '../lib/accents'
import { PUSHBACK_OUTCOME_LABELS, panelistName } from '../lib/labels'
import { Avatar } from './Avatar'
import { EvaluationScores, ScaleCaption, ScoreRow } from './Scores'
import { Sources } from './Sources'
import { Button, Card, Heading } from './ui'

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
    <Card className="space-y-3">
      <h3 className="text-xs font-semibold tracking-wider text-muted uppercase">{title}</h3>
      {children}
    </Card>
  )
}

function Overview({ report }: { report: DefenseReport }) {
  return (
    <Section title="Overall">
      <ScoreRow clarity={report.overall_avg_clarity} depth={report.overall_avg_depth} grounding={report.overall_avg_grounding} />
      <ScaleCaption />
      <p className="text-sm">
        <span className="font-medium">Difficulty by turn: </span>
        {report.difficulty_trajectory.join(' → ')}
      </p>
      <p className="leading-relaxed">
        {report.narrative_fallback_used || !report.narrative ? 'Narrative unavailable this session' : report.narrative}
      </p>
    </Section>
  )
}

function Transcript({ transcript }: { transcript: TranscriptTurn[] }) {
  return (
    <Section title="Transcript">
      <ol className="space-y-6">
        {transcript.map((turn) => (
          <li key={turn.turn} className="space-y-3">
            <div className="flex items-center gap-3">
              <Avatar icon={turn.panelist.icon} archetypeKey={turn.panelist.archetype_key} size="sm" />
              <p className="text-sm">
                <span className="font-medium">{panelistName(turn.panelist.name)}</span>
                <span className={accentFor(turn.panelist.archetype_key).text}> · {turn.panelist.title}</span>
                <span className="text-muted"> · question {turn.turn}, difficulty {turn.difficulty}</span>
              </p>
            </div>
            <p className="font-serif leading-relaxed">{turn.question}</p>
            <blockquote className="border-l-2 border-line pl-3 text-sm whitespace-pre-line">{turn.answer}</blockquote>
            <EvaluationScores evaluation={turn.evaluation} />
            {turn.evaluation.primary_gap && <p className="text-sm">What was missing: {turn.evaluation.primary_gap}</p>}
            <Sources sources={turn.sources} />
          </li>
        ))}
      </ol>
    </Section>
  )
}

export function ReportScreen({ result, options, onRestart }: { result: ReportResponse; options: PanelOptions | null; onRestart: () => void }) {
  const { report, transcript } = result
  const byKey = seats(transcript, options)
  return (
    <div className="space-y-4">
      <div>
        <Heading>Your defense report</Heading>
        <p className="text-sm text-muted">
          {result.topic} · {result.domain}
        </p>
      </div>

      <Overview report={report} />

      <Section title="Per panelist">
        <ul className="space-y-4">
          {report.panelist_sections.map((section) => {
            const seat = byKey.get(section.archetype_key)
            return (
              <li key={section.archetype_key} className="space-y-2">
                <div className="flex items-center gap-3">
                  {seat && <Avatar icon={seat.icon} archetypeKey={section.archetype_key} size="sm" />}
                  <p className="text-sm">
                    <span className="font-medium">{panelistName(section.panelist_name)}</span>
                    {seat && <span className={accentFor(section.archetype_key).text}> · {seat.title}</span>}
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

      <Section title="Answer suggestions">
        {report.suggestions_fallback_used ? (
          <p className="text-sm">Suggestions unavailable this session</p>
        ) : !report.answer_suggestions?.length ? (
          <p className="text-sm">No suggestions for this session</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {report.answer_suggestions.map((s) => (
              <li key={s.turn_index}>
                <span className="font-medium">Turn {s.turn_index + 1}:</span> {s.suggestion}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Transcript transcript={transcript} />

      <Button onClick={onRestart}>Start a new defense</Button>
    </div>
  )
}
