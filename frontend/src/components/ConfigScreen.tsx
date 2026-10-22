// Panel config + upload (D14, D15). Usable at once; the server wakes in the background (D25).
import { useId } from 'react'
import { limits } from '../api/constraints.gen'
import type { PanelOptions } from '../api/types'
import { DEFENSE_TYPE_LABELS, DEFENSE_TYPES, OTHER_SUBTYPE_LABELS, OTHER_SUBTYPES } from '../lib/labels'
import { rosterFor, seatedKeys, toggleArchetype, withDefenseType, withSubtype } from '../lib/panel'
import { MAX_UPLOAD_MB } from '../lib/upload'
import type { Draft, Notice, QuotaView } from '../state/flow'
import { Avatar } from './Avatar'
import { NoticeBanner } from './NoticeBanner'
import { QuotaLine, quotaUsedUp } from './QuotaLine'
import { Button, Card } from './ui'

const PANEL = limits.PanelConfig

type Props = {
  draft: Draft
  options: PanelOptions | null
  quota: QuotaView
  notice: Notice | null
  onChange: (draft: Partial<Draft>) => void
  onSubmit: () => void
}

const fieldClass = 'mt-1 w-full rounded-lg border border-line bg-card px-3 py-2 text-base focus:border-brand focus:outline-none'

export function ConfigScreen({ draft, options, quota, notice, onChange, onSubmit }: Props) {
  const { panel, file } = draft
  const ids = { file: useId(), subtype: useId(), domain: useId(), topic: useId() }
  const setPanel = (next: typeof panel) => onChange({ panel: next })
  const blocked = quotaUsedUp(quota)

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit()
      }}
    >
      <Card>
        <label htmlFor={ids.file} className="block text-sm font-medium">
          Your document (PDF, up to {MAX_UPLOAD_MB} MB)
        </label>
        <input
          id={ids.file}
          type="file"
          accept="application/pdf,.pdf"
          className="mt-2 block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-brand-soft file:px-3 file:py-2 file:text-brand"
          onChange={(event) => onChange({ file: event.target.files?.[0] ?? null })}
        />
        {file && (
          <p className="mt-2 truncate text-sm text-muted">
            {file.name} - {(file.size / (1024 * 1024)).toFixed(1)} MB
          </p>
        )}
      </Card>

      <Card className="space-y-4">
        <fieldset>
          <legend className="text-sm font-medium">Defense type</legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {DEFENSE_TYPES.map((type) => (
              <label
                key={type}
                className="cursor-pointer rounded-full border border-line px-3 py-1.5 text-sm has-checked:border-brand has-checked:bg-brand-soft has-checked:text-brand"
              >
                <input
                  type="radio"
                  name="defense_type"
                  value={type}
                  checked={panel.defense_type === type}
                  onChange={() => setPanel(withDefenseType(panel, type))}
                  className="sr-only"
                />
                {DEFENSE_TYPE_LABELS[type]}
              </label>
            ))}
          </div>
        </fieldset>

        {panel.defense_type === 'other' && (
          <div>
            <label htmlFor={ids.subtype} className="block text-sm font-medium">
              Kind of panel
            </label>
            <select
              id={ids.subtype}
              className={fieldClass}
              value={panel.other_subtype ?? OTHER_SUBTYPES[0]}
              onChange={(event) => {
                const subtype = OTHER_SUBTYPES.find((s) => s === event.target.value)
                if (subtype) setPanel(withSubtype(panel, subtype))
              }}
            >
              {OTHER_SUBTYPES.map((subtype) => (
                <option key={subtype} value={subtype}>
                  {OTHER_SUBTYPE_LABELS[subtype]}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor={ids.domain} className="block text-sm font-medium">
              Domain / discipline <span className="font-normal text-muted">(optional)</span>
            </label>
            <input
              id={ids.domain}
              className={fieldClass}
              maxLength={PANEL.domain.maxLength}
              value={panel.domain ?? ''}
              onChange={(event) => setPanel({ ...panel, domain: event.target.value || null })}
            />
          </div>
          <div>
            <label htmlFor={ids.topic} className="block text-sm font-medium">
              Research title / topic <span className="font-normal text-muted">(optional)</span>
            </label>
            <input
              id={ids.topic}
              className={fieldClass}
              maxLength={PANEL.topic.maxLength}
              value={panel.topic ?? ''}
              onChange={(event) => setPanel({ ...panel, topic: event.target.value || null })}
            />
          </div>
        </div>
        <p className="text-sm text-muted">Leave these blank and the panel reads them from your document.</p>
      </Card>

      <Card>
        <PanelPicker options={options} draft={draft} onChange={setPanel} />
      </Card>

      <Card>
        <fieldset>
          <legend className="text-sm font-medium">Starting difficulty</legend>
          <div className="mt-2 flex gap-2">
            {range(PANEL.starting_difficulty.minimum, PANEL.starting_difficulty.maximum).map((level) => (
              <label
                key={level}
                className="flex size-10 cursor-pointer items-center justify-center rounded-lg border border-line text-sm has-checked:border-brand has-checked:bg-brand-soft has-checked:text-brand"
              >
                <input
                  type="radio"
                  name="starting_difficulty"
                  value={level}
                  checked={panel.starting_difficulty === level}
                  onChange={() => setPanel({ ...panel, starting_difficulty: level })}
                  className="sr-only"
                />
                {level}
              </label>
            ))}
          </div>
          <p className="mt-2 text-sm text-muted">How hard the panel opens. It adapts from there based on your answers.</p>
        </fieldset>
      </Card>

      {notice && <NoticeBanner notice={notice} onRetry={notice.kind === 'network' || notice.kind === 'busy' ? onSubmit : undefined} />}

      <div className="space-y-2">
        <QuotaLine quota={quota} />
        <Button type="submit" className="w-full sm:w-auto" disabled={file === null || blocked}>
          Upload and convene the panel
        </Button>
      </div>
    </form>
  )
}

function range(from: number, to: number): number[] {
  return Array.from({ length: to - from + 1 }, (_, i) => from + i)
}

function PanelPicker({ options, draft, onChange }: { options: PanelOptions | null; draft: Draft; onChange: (panel: Draft['panel']) => void }) {
  const { panel } = draft
  const roster = options && rosterFor(options, panel)
  if (options === null || roster === null) {
    return (
      <>
        <p className="text-sm font-medium">Panel</p>
        <p className="mt-1 text-sm text-muted">Loading the panel roster. If you start before it loads, you get the recommended panel.</p>
      </>
    )
  }
  const seated = seatedKeys(options, panel)
  return (
    <fieldset>
      <legend className="text-sm font-medium">
        Panel <span className="font-normal text-muted">(choose up to {options.max_domain_panelists})</span>
      </legend>
      <ul className="mt-2 space-y-2">
        {roster.archetypes.map((archetype) => {
          const checked = seated.includes(archetype.archetype_key)
          const full = !checked && seated.length >= options.max_domain_panelists
          return (
            <li key={archetype.archetype_key}>
              <label className={`flex items-center gap-3 rounded-lg border p-2 ${checked ? 'border-brand bg-brand-soft' : 'border-line'} ${full ? 'opacity-50' : 'cursor-pointer'}`}>
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={full || (checked && seated.length === 1)}
                  onChange={() => onChange({ ...panel, archetype_keys: toggleArchetype(options, panel, archetype.archetype_key) })}
                  className="size-4 accent-brand"
                />
                <Avatar icon={archetype.icon} archetypeKey={archetype.archetype_key} size="sm" />
                <span className="text-sm">{archetype.title}</span>
              </label>
            </li>
          )
        })}
        <li className="flex items-center gap-3 rounded-lg border border-dashed border-line p-2 text-sm text-muted">
          <input type="checkbox" checked disabled className="size-4" aria-label={`${options.devils_advocate.title} always sits`} />
          <Avatar icon={options.devils_advocate.icon} archetypeKey={options.devils_advocate.archetype_key} size="sm" />
          <span>{options.devils_advocate.title}, always on the panel</span>
        </li>
      </ul>
      {panel.archetype_keys !== null && (
        <Button variant="secondary" className="mt-3" onClick={() => onChange({ ...panel, archetype_keys: null })}>
          Use recommended panel
        </Button>
      )}
    </fieldset>
  )
}
