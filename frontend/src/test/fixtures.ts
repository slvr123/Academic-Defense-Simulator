// Response bodies typed with the generated API types, so a schema change breaks the
// fixtures at compile time instead of letting them drift.
import type {
  AnswerResponse,
  Evaluation,
  PanelistOut,
  PanelOptions,
  QuestionOut,
  QuotaStatus,
  ReportResponse,
  StartResponse,
} from '../api/types'

export const quota = (remaining: number): QuotaStatus => ({
  used: 11 - remaining,
  cap: 11,
  remaining,
  resets_at: '2026-10-23T00:00:00-07:00', // midnight Pacific = Fri 3:00 PM in Manila
})

const option = (archetype_key: string, title: string, icon: string) => ({ archetype_key, title, icon })

export const panelOptions: PanelOptions = {
  max_domain_panelists: 3,
  devils_advocate: option('devils_advocate', "Devil's Advocate", 'researcher'),
  rosters: [
    {
      defense_type: 'thesis',
      other_subtype: null,
      archetypes: [
        option('methodology_expert', 'Methodology Expert', 'engineer'),
        option('literature_theory_specialist', 'Literature & Theory Specialist', 'gorilla'),
        option('problem_objectives_reviewer', 'Problem & Objectives Reviewer', 'hacker'),
        option('results_conclusions_reviewer', 'Results & Conclusions Reviewer', 'professor'),
      ],
    },
    {
      defense_type: 'other',
      other_subtype: 'certification_interview',
      archetypes: [
        option('technical_implementation_reviewer', 'Technical Implementation Reviewer', 'researcher'),
        option('industry_practice_reviewer', 'Industry Practice Reviewer', 'scholar'),
      ],
    },
  ],
}

export const reyes: PanelistOut = { archetype_key: 'methodology_expert', name: 'Reyes', title: 'Methodology Expert', icon: 'engineer' }
export const santos: PanelistOut = { archetype_key: 'devils_advocate', name: 'Santos', title: "Devil's Advocate", icon: 'researcher' }

export const question = (turn: number, text: string, panelist: PanelistOut = reyes): QuestionOut => ({
  turn,
  panelist,
  question: text,
  difficulty: 2,
  sources: [
    {
      text: `Source passage for turn ${turn}: the instrument was adapted from ISO/IEC 25010.`,
      page_start: 12,
      page_end: 13,
      score: 0.8123,
      origin: 'new_topic',
    },
  ],
})

export const startResponse: StartResponse = {
  domain: 'Information Technology',
  topic: 'Library Management System',
  panel: [reyes, santos],
  question: question(1, 'How did you validate your survey instrument?'),
}

export const evaluation: Evaluation = {
  clarity: 4,
  depth: 3,
  grounding: 4,
  total: 11,
  difficulty_delta: 0,
  primary_gap: 'No reliability coefficient reported.',
  answer_summary: 'Cites ISO 25010 but gives no reliability figure.',
}

export const nextQuestion: AnswerResponse = {
  evaluation,
  next_question: question(2, 'Why thirty respondents?', santos),
  done: false,
}

export const lastAnswer: AnswerResponse = { evaluation, next_question: null, done: true }

export const report: ReportResponse = {
  domain: 'Information Technology',
  topic: 'Library Management System',
  report: {
    difficulty_trajectory: [2, 2],
    overall_avg_clarity: 3.5,
    overall_avg_depth: 3,
    overall_avg_grounding: 4,
    panelist_sections: [
      {
        archetype_key: 'methodology_expert',
        panelist_name: 'Reyes',
        turns_taken: 1,
        avg_clarity: 4,
        avg_depth: 3,
        avg_grounding: 4,
        primary_gaps: ['No reliability coefficient reported.'],
      },
    ],
    pushback_events: [],
    narrative: 'You grounded most answers in the document.',
    narrative_fallback_used: false,
    answer_suggestions: [
      { turn_index: 0, suggestion: 'Report Cronbach’s alpha for the instrument.', grounding_reference: 'p. 12' },
    ],
    suggestions_fallback_used: false,
  },
  transcript: [
    {
      turn: 1,
      panelist: reyes,
      question: 'How did you validate your survey instrument?',
      difficulty: 2,
      sources: question(1, '').sources,
      answer: 'We adapted ISO 25010.',
      evaluation,
    },
  ],
}

export const pdf = (bytes = 1024, name = 'thesis.pdf') =>
  new File([new Uint8Array(Math.min(bytes, 16))], name, { type: 'application/pdf' })
