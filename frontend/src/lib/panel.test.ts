import { describe, expect, it } from 'vitest'
import type { PanelConfig } from '../api/types'
import { panelOptions } from '../test/fixtures'
import { DEFENSE_TYPE_LABELS, OTHER_SUBTYPE_LABELS } from './labels'
import { heardFrom, seatedKeys, seatedPanel, toggleArchetype, withDefenseType, withSubtype } from './panel'
import realOptions from '../test/panelOptions.real.json'
import type { PanelOptions } from '../api/types'

const thesis: PanelConfig = {
  defense_type: 'thesis',
  other_subtype: null,
  domain: null,
  topic: null,
  archetype_keys: null,
  starting_difficulty: 2,
}

describe('panel choices', () => {
  it('seats the top of the roster when no panelists are chosen', () => {
    expect(seatedKeys(panelOptions, thesis)).toEqual([
      'methodology_expert',
      'literature_theory_specialist',
      'problem_objectives_reviewer',
    ])
  })

  it("puts the Devil's Advocate last", () => {
    expect(seatedPanel(panelOptions, thesis).map((a) => a.archetype_key).at(-1)).toBe('devils_advocate')
  })

  it('removes, refuses a fourth, keeps at least one, and keeps roster order', () => {
    const two = toggleArchetype(panelOptions, thesis, 'literature_theory_specialist')
    expect(two).toEqual(['methodology_expert', 'problem_objectives_reviewer'])
    const full = { ...thesis, archetype_keys: ['methodology_expert', 'problem_objectives_reviewer', 'literature_theory_specialist'] }
    expect(toggleArchetype(panelOptions, full, 'results_conclusions_reviewer')).toEqual([
      'methodology_expert',
      'literature_theory_specialist',
      'problem_objectives_reviewer',
    ])
    const one = { ...thesis, archetype_keys: ['methodology_expert'] }
    expect(toggleArchetype(panelOptions, one, 'methodology_expert')).toEqual(['methodology_expert'])
  })

  it('resets to the recommended panel and sets a subtype when the type changes', () => {
    const chosen = { ...thesis, archetype_keys: ['methodology_expert'] }
    expect(withDefenseType(chosen, 'other')).toEqual({ ...thesis, defense_type: 'other', other_subtype: 'oral_comps' })
    expect(withDefenseType({ ...thesis, defense_type: 'other', other_subtype: 'grant_defense' }, 'capstone')).toMatchObject({
      defense_type: 'capstone',
      other_subtype: null,
    })
    const other = withSubtype(withDefenseType(thesis, 'other'), 'certification_interview')
    expect(seatedKeys(panelOptions, other)).toEqual(['technical_implementation_reviewer', 'industry_practice_reviewer'])
  })
})

describe('labels (D22)', () => {
  it('never shows a raw enum value', () => {
    for (const [value, label] of Object.entries({ ...DEFENSE_TYPE_LABELS, ...OTHER_SUBTYPE_LABELS })) {
      expect(label).not.toBe(value)
      expect(label).not.toContain('_')
    }
    expect(OTHER_SUBTYPE_LABELS.oral_comps).toBe('Oral comps')
  })
})

describe('the seating order /start uses (M12 D1)', () => {
  const live = realOptions as PanelOptions
  const keys = (panel: PanelConfig) => seatedPanel(live, panel).map((a) => a.archetype_key)

  it("seats the thesis recommended panel from Chapter 1 forward, the Devil's Advocate last", () => {
    const recommended = { ...thesis, archetype_keys: ['methodology_expert', 'problem_objectives_reviewer', 'results_conclusions_reviewer'] }
    expect(keys(recommended)).toEqual(['problem_objectives_reviewer', 'methodology_expert', 'results_conclusions_reviewer', 'devils_advocate'])
  })

  it('keeps roster order between panelists of the same home chapter', () => {
    const capstone: PanelConfig = { ...thesis, defense_type: 'capstone', archetype_keys: ['technical_implementation_reviewer', 'methodology_expert', 'results_conclusions_reviewer'] }
    expect(keys(capstone)).toEqual(['technical_implementation_reviewer', 'methodology_expert', 'results_conclusions_reviewer', 'devils_advocate'])
  })
})

describe('heardFrom (M12 D2)', () => {
  const panel = ['problem_objectives_reviewer', 'methodology_expert', 'results_conclusions_reviewer', 'devils_advocate'].map((archetype_key) => ({ archetype_key }))

  it('counts distinct domain askers, the open question included, out of the domain seats', () => {
    expect(heardFrom(panel, ['problem_objectives_reviewer'])).toEqual({ count: 1, of: 3 })
    // A follow-up from the same panelist hears no one new.
    expect(heardFrom(panel, ['problem_objectives_reviewer', 'problem_objectives_reviewer'])).toEqual({ count: 1, of: 3 })
    // The Devil's Advocate counts on neither side.
    const all = ['problem_objectives_reviewer', 'methodology_expert', 'results_conclusions_reviewer', 'devils_advocate']
    expect(heardFrom(panel, all)).toEqual({ count: 3, of: 3 })
  })
})
