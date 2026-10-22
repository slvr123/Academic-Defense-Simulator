// Each archetype's accent from the theme tokens in index.css. Whole class names, not
// built strings, so Tailwind finds them when it scans the source.
type Accent = { border: string; text: string; bg: string }

const ACCENTS: Record<string, Accent> = {
  methodology_expert: { border: 'border-accent-methodology', text: 'text-accent-methodology', bg: 'bg-accent-methodology' },
  literature_theory_specialist: { border: 'border-accent-literature', text: 'text-accent-literature', bg: 'bg-accent-literature' },
  technical_implementation_reviewer: { border: 'border-accent-technical', text: 'text-accent-technical', bg: 'bg-accent-technical' },
  ethics_practicality_reviewer: { border: 'border-accent-ethics', text: 'text-accent-ethics', bg: 'bg-accent-ethics' },
  problem_objectives_reviewer: { border: 'border-accent-problem', text: 'text-accent-problem', bg: 'bg-accent-problem' },
  statistical_analysis_reviewer: { border: 'border-accent-statistics', text: 'text-accent-statistics', bg: 'bg-accent-statistics' },
  results_conclusions_reviewer: { border: 'border-accent-results', text: 'text-accent-results', bg: 'bg-accent-results' },
  industry_practice_reviewer: { border: 'border-accent-industry', text: 'text-accent-industry', bg: 'bg-accent-industry' },
  devils_advocate: { border: 'border-accent-devil', text: 'text-accent-devil', bg: 'bg-accent-devil' },
}

// An archetype added to the backend before this map shows in neutral ink, not broken.
const NEUTRAL: Accent = { border: 'border-muted', text: 'text-ink', bg: 'bg-muted' }

export function accentFor(archetypeKey: string): Accent {
  return ACCENTS[archetypeKey] ?? NEUTRAL
}

export const ACCENT_KEYS = Object.keys(ACCENTS)
