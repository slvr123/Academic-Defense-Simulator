// Frontend copy about each panelist role (M10 N6, N7). Keyed by v1's archetype keys;
// a key the backend adds later falls back to its own title and no "looks for" line.

/** Short role names for preset rows ("Methodology · Problem & Objectives"). */
const SHORT_TITLES: Record<string, string> = {
  methodology_expert: 'Methodology',
  literature_theory_specialist: 'Literature & Theory',
  technical_implementation_reviewer: 'Technical Implementation',
  ethics_practicality_reviewer: 'Ethics & Practicality',
  problem_objectives_reviewer: 'Problem & Objectives',
  statistical_analysis_reviewer: 'Statistical Analysis',
  results_conclusions_reviewer: 'Results & Conclusions',
  industry_practice_reviewer: 'Industry Practice',
}

export function shortTitle(archetypeKey: string, title: string): string {
  return SHORT_TITLES[archetypeKey] ?? title
}

// One line per role, summarising v1's archetype_focus (academic_defense_simulator/
// prompts/panelist_prompts.py, ARCHETYPE_CONFIG). Shown while /start runs: what each
// role looks for, never a claim about what it is reading now.
const LOOKS_FOR: Record<string, string> = {
  methodology_expert: 'Research design, sampling and data choices, and whether your approach answers your question',
  literature_theory_specialist: 'Gaps in the cited work, theoretical grounding, and how your claims sit against existing work',
  technical_implementation_reviewer: 'Build decisions and technical trade-offs, and whether the build matches what you claim',
  ethics_practicality_reviewer: 'Real-world use, limitations, deployment and ethical considerations',
  problem_objectives_reviewer: 'Whether your problem is well defined and scoped, and your objectives match it',
  statistical_analysis_reviewer: 'Your tests and metrics, their assumptions, and whether the numbers support your claims',
  results_conclusions_reviewer: 'Whether your conclusions follow from your findings and your claims fit the evidence',
  industry_practice_reviewer: 'How your work compares to professional practice, and what adopting it would take',
  devils_advocate: "Contests the strongest claim made so far, tested against your paper",
}

export function looksFor(archetypeKey: string): string | null {
  return LOOKS_FOR[archetypeKey] ?? null
}

/** Shown beside the Literature & Theory Specialist under Custom while M9's lit_review_access is "none". */
export const LITERATURE_NOTE = "Doesn't read the literature review in this version."
export const LITERATURE_KEY = 'literature_theory_specialist'
