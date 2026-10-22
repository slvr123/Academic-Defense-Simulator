// Panel choices, from GET /panel-options (D21). PanelConfig's archetype_keys is null
// for the recommended panel, as in v1's "Use recommended panel".
import type { ArchetypeOption, DefenseType, OtherSubtype, PanelConfig, PanelOptions, PanelRoster } from '../api/types'
import { OTHER_SUBTYPES } from './labels'

export function rosterFor(options: PanelOptions, panel: PanelConfig): PanelRoster | null {
  return (
    options.rosters.find((r) => r.defense_type === panel.defense_type && r.other_subtype === panel.other_subtype) ?? null
  )
}

/** The archetypes seated for this config, in v1's priority order. */
export function seatedKeys(options: PanelOptions, panel: PanelConfig): string[] {
  const roster = rosterFor(options, panel)
  if (roster === null) return []
  if (panel.archetype_keys !== null) return panel.archetype_keys
  return roster.archetypes.slice(0, options.max_domain_panelists).map((a) => a.archetype_key)
}

/** The seated archetypes with titles and icons, Devil's Advocate last as v1 seats it. */
export function seatedPanel(options: PanelOptions, panel: PanelConfig): ArchetypeOption[] {
  const roster = rosterFor(options, panel)
  if (roster === null) return []
  const keys = seatedKeys(options, panel)
  return [...roster.archetypes.filter((a) => keys.includes(a.archetype_key)), options.devils_advocate]
}

/** Add or remove one panelist, keeping at least one and at most the maximum, in roster order. */
export function toggleArchetype(options: PanelOptions, panel: PanelConfig, key: string): string[] {
  const roster = rosterFor(options, panel)
  if (roster === null) return []
  const chosen = new Set(seatedKeys(options, panel))
  if (chosen.has(key)) {
    if (chosen.size > 1) chosen.delete(key)
  } else if (chosen.size < options.max_domain_panelists) {
    chosen.add(key)
  }
  return roster.archetypes.map((a) => a.archetype_key).filter((k) => chosen.has(k))
}

/** A new defense type resets the panel to its recommended one, and "other" needs a subtype. */
export function withDefenseType(panel: PanelConfig, defenseType: DefenseType): PanelConfig {
  return {
    ...panel,
    defense_type: defenseType,
    other_subtype: defenseType === 'other' ? (panel.other_subtype ?? OTHER_SUBTYPES[0]) : null,
    archetype_keys: null,
  }
}

export function withSubtype(panel: PanelConfig, subtype: OtherSubtype): PanelConfig {
  return { ...panel, other_subtype: subtype, archetype_keys: null }
}
