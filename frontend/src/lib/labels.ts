// What users see for each enum value (D22). Records over the generated unions, so the
// build fails if the backend adds a defense type or subtype without a label here.
import type { DefenseType, OtherSubtype, Source } from '../api/types'

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

// Object.keys loses the key type; these keep it, in the label maps' order.
export const DEFENSE_TYPES = Object.keys(DEFENSE_TYPE_LABELS) as DefenseType[]
export const OTHER_SUBTYPES = Object.keys(OTHER_SUBTYPE_LABELS) as OtherSubtype[]

export function panelistName(surname: string): string {
  return `Dr. ${surname}`
}

export const SOURCE_ORIGIN_LABELS: Record<Source['origin'], string> = {
  new_topic: 'New topic',
  follow_up: 'Follow-up on an earlier passage',
  devils_advocate: "Devil's Advocate, contesting an earlier passage",
}

/** Pages as a PDF viewer numbers them (1-based, as the API sends them). */
export function pageRange(start: number, end: number): string {
  return start === end ? `Page ${start}` : `Pages ${start}-${end}`
}
