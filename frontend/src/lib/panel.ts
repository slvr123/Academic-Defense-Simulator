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

/** The seated archetypes with titles and icons, in the order /start seats them (the first
 * asks first): by home chapter, ties in roster order (M12 D1, backend/opening_order.py),
 * Devil's Advocate last as v1 seats it. */
export function seatedPanel(options: PanelOptions, panel: PanelConfig): ArchetypeOption[] {
  const roster = rosterFor(options, panel)
  if (roster === null) return []
  const byKey = new Map(roster.archetypes.map((a) => [a.archetype_key, a]))
  const seated = seatedKeys(options, panel).flatMap((key) => byKey.get(key) ?? [])
  // Array.prototype.sort is stable, so ties keep their roster order, as on the backend.
  const byChapter = [...seated].sort((a, b) => (a.home_chapter ?? Infinity) - (b.home_chapter ?? Infinity))
  return [...byChapter, options.devils_advocate]
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

/** v1's DEVILS_ADVOCATE_KEY: always seated last, and drawn with a dashed plate on the bench. */
export const DEVILS_ADVOCATE_KEY = 'devils_advocate'

/** The header's "k/n heard" (M12 D2): distinct domain panelists who have asked, the open
 * question's asker included, out of the domain panelists seated. The Devil's Advocate
 * isn't counted on either side: it speaks once every domain panelist has. */
export function heardFrom(panel: { archetype_key: string }[], askers: string[]): { count: number; of: number } {
  const domain = new Set(panel.map((p) => p.archetype_key).filter((key) => key !== DEVILS_ADVOCATE_KEY))
  return { count: new Set(askers.filter((key) => domain.has(key))).size, of: domain.size }
}
