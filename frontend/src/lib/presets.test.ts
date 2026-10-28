// M10 N7's validity test: every preset offered for a panel type is one /panel-options
// seats there, checked against the real response (backend/tests/test_panel_options.py
// keeps the copy current). A preset that doesn't fit a type is hidden, never sent.
import { describe, expect, it } from 'vitest'
import type { PanelConfig, PanelOptions } from '../api/types'
import realOptions from '../test/panelOptions.real.json'
import { rosterFor } from './panel'
import { allPresets, firstPreset, presetsFor, resolvedKeys } from './presets'

const options = realOptions as PanelOptions

const panelFor = (defense_type: PanelConfig['defense_type'], other_subtype: PanelConfig['other_subtype']): PanelConfig => ({
  defense_type,
  other_subtype,
  domain: null,
  topic: null,
  archetype_keys: null,
  starting_difficulty: 2,
})

const TYPES = options.rosters.map((r) => panelFor(r.defense_type, r.other_subtype))
const typeName = (panel: PanelConfig) => panel.other_subtype ?? panel.defense_type

describe('panel presets against the real /panel-options (M10 N7)', () => {
  it('offers exactly these presets per panel type', () => {
    const table = Object.fromEntries(TYPES.map((panel) => [typeName(panel), presetsFor(options, panel).map((p) => p.id)]))
    expect(table).toEqual({
      thesis: ['recommended', 'technical', 'research'],
      capstone: ['recommended', 'technical', 'research', 'impact'],
      oral_comps: ['recommended'],
      scholarship_panel: ['recommended'],
      certification_interview: ['impact'],
      grant_defense: ['recommended'],
    })
  })

  it('offers only presets whose every panelist the roster seats, within the panel size', () => {
    for (const panel of TYPES) {
      const seatable = new Set(rosterFor(options, panel)?.archetypes.map((a) => a.archetype_key))
      for (const preset of presetsFor(options, panel)) {
        expect(preset.keys.length).toBeLessThanOrEqual(options.max_domain_panelists)
        for (const key of preset.keys) expect(seatable.has(key), `${preset.id} on ${typeName(panel)}: ${key}`).toBe(true)
      }
    }
  })

  it('never puts the Literature & Theory Specialist in a preset while lit_review_access is none', () => {
    for (const panel of TYPES) {
      for (const preset of allPresets(panel.defense_type)) expect(preset.keys).not.toContain('literature_theory_specialist')
    }
  })

  it('selects the first offered preset, and never sends one the type can\'t seat', () => {
    const certification = panelFor('other', 'certification_interview')
    expect(firstPreset(options, certification)).toBe('impact')
    expect(resolvedKeys(options, certification, 'recommended')).toEqual([
      'industry_practice_reviewer',
      'ethics_practicality_reviewer',
      'technical_implementation_reviewer',
    ])
    expect(firstPreset(options, panelFor('thesis', null))).toBe('recommended')
    expect(resolvedKeys(options, panelFor('thesis', null), 'impact')).toEqual([
      'methodology_expert',
      'problem_objectives_reviewer',
      'results_conclusions_reviewer',
    ])
  })
})
