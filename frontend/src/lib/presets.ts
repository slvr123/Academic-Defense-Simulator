// Panel presets (M10 N7): frontend data over v1's existing archetype keys. A preset is
// offered for a defense type only when /panel-options seats every one of its panelists
// there; otherwise it's hidden for that type, never sent. The Literature & Theory
// Specialist is in no preset while the backend's lit_review_access is "none" (M9).
import type { DefenseType, PanelConfig, PanelOptions } from '../api/types'
import { rosterFor } from './panel'

export type PresetId = 'recommended' | 'technical' | 'research' | 'impact' | 'custom'

export type Preset = { id: Exclude<PresetId, 'custom'>; label: string; keys: string[] }

const RECOMMENDED: Record<DefenseType, string[]> = {
  thesis: ['methodology_expert', 'problem_objectives_reviewer', 'results_conclusions_reviewer'],
  capstone: ['technical_implementation_reviewer', 'methodology_expert', 'results_conclusions_reviewer'],
  other: ['methodology_expert', 'problem_objectives_reviewer', 'ethics_practicality_reviewer'],
}

const RECOMMENDED_LABEL: Record<DefenseType, string> = {
  thesis: 'Recommended for a thesis',
  capstone: 'Recommended for a capstone',
  other: 'Recommended for this defense',
}

const SHARED: Preset[] = [
  { id: 'technical', label: 'Technical-heavy', keys: ['technical_implementation_reviewer', 'methodology_expert', 'statistical_analysis_reviewer'] },
  { id: 'research', label: 'Research-heavy', keys: ['problem_objectives_reviewer', 'statistical_analysis_reviewer', 'results_conclusions_reviewer'] },
  { id: 'impact', label: 'Real-world impact', keys: ['industry_practice_reviewer', 'ethics_practicality_reviewer', 'technical_implementation_reviewer'] },
]

/** Every preset for a defense type, valid or not: the Recommended row first. */
export function allPresets(defenseType: DefenseType): Preset[] {
  return [{ id: 'recommended', label: RECOMMENDED_LABEL[defenseType], keys: RECOMMENDED[defenseType] }, ...SHARED]
}

/** The presets this panel type can seat, per /panel-options, in display order. */
export function presetsFor(options: PanelOptions, panel: PanelConfig): Preset[] {
  const roster = rosterFor(options, panel)
  if (roster === null) return []
  const seatable = new Set(roster.archetypes.map((a) => a.archetype_key))
  return allPresets(panel.defense_type).filter(
    (preset) => preset.keys.length <= options.max_domain_panelists && preset.keys.every((key) => seatable.has(key)),
  )
}

/** The archetype keys to send: the preset's, or the custom choice. A preset this type
 * can't seat falls back to the first one it can, and with no valid preset at all to the
 * backend's recommended panel (null). */
export function resolvedKeys(options: PanelOptions, panel: PanelConfig, preset: PresetId): string[] | null {
  if (preset === 'custom') return panel.archetype_keys
  const offered = presetsFor(options, panel)
  return (offered.find((p) => p.id === preset) ?? offered[0])?.keys ?? null
}

/** The preset to select when the panel type changes: the first one offered. */
export function firstPreset(options: PanelOptions | null, panel: PanelConfig): PresetId {
  if (options === null) return 'recommended'
  return presetsFor(options, panel)[0]?.id ?? 'custom'
}
