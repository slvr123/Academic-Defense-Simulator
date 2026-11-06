// What users see for each enum value (D22). Records over the generated unions, so the
// build fails if the backend adds a defense type or subtype without a label here.
import type { DefenseType, OtherSubtype, PanelConfig, PushbackOutcome, Source } from '../api/types'

export const DEFENSE_TYPE_LABELS: Record<DefenseType, string> = {
  thesis: 'Thesis',
  capstone: 'Capstone',
  other: 'Other',
}

export const OTHER_SUBTYPE_LABELS: Record<OtherSubtype, string> = {
  oral_comps: 'Oral comps',
  scholarship_panel: 'Scholarship panel',
  certification_interview: 'Certification interview',
  grant_defense: 'Grant defense',
}

// M16: what the panel speaks. The interface and the debrief stay English either way.
export const PANEL_LANGUAGE_LABELS: Record<PanelConfig['panel_language'], string> = {
  english: 'English',
  taglish: 'Taglish',
}

// Object.keys loses the key type; these keep it, in the label maps' order.
export const DEFENSE_TYPES = Object.keys(DEFENSE_TYPE_LABELS) as DefenseType[]
export const OTHER_SUBTYPES = Object.keys(OTHER_SUBTYPE_LABELS) as OtherSubtype[]
export const PANEL_LANGUAGES = Object.keys(PANEL_LANGUAGE_LABELS) as PanelConfig['panel_language'][]

/** The session header's subtitle: "<topic> · Thesis defense", plus " · Taglish" for a Taglish panel (M16). */
export function sessionSubtitle(topic: string, panel: Pick<PanelConfig, 'defense_type' | 'panel_language'>): string {
  const parts = [topic, `${DEFENSE_TYPE_LABELS[panel.defense_type]} defense`]
  if (panel.panel_language === 'taglish') parts.push(PANEL_LANGUAGE_LABELS.taglish)
  return parts.join(' · ')
}

export function panelistName(surname: string): string {
  return `Dr. ${surname}`
}

export const SOURCE_ORIGIN_LABELS: Record<Source['origin'], string> = {
  new_topic: 'New topic',
  follow_up: 'Follow-up on an earlier passage',
  devils_advocate: 'Terror Panelist, contesting an earlier passage',
}

/** Pages as a PDF viewer numbers them (1-based, as the API sends them), labelled "PDF" because
 * printed page numbers differ: sample3's printed "page 3" is PDF page 5 (M7 J6). */
export function pageRange(start: number, end: number): string {
  return start === end ? `PDF page ${start}` : `PDF pages ${start}–${end}`
}

/** A skipped range on the preparing screen (M10 N6): "p. 3" or "pp. 3–4", PDF page numbers. */
export function pageSpan(start: number, end: number): string {
  return start === end ? `p. ${start}` : `pp. ${start}–${end}`
}

export const PUSHBACK_OUTCOME_LABELS: Record<PushbackOutcome, string> = {
  recovered: 'recovered',
  held: 'held',
  deteriorated: 'deteriorated',
}
