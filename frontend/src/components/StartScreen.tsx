// The start screen (M10 N3, canvas Start.dc.html): the hero and the no-upload paths on
// the left, the form on the right, stacked on phones. Usable at once; the server wakes
// in the background (D25), and presets are checked against /panel-options (N7).
import { type DragEvent, type ReactNode, useId, useState } from 'react'
import { limits } from '../api/constraints.gen'
import type { PanelOptions } from '../api/types'
import { DEFENSE_TYPE_LABELS, DEFENSE_TYPES, OTHER_SUBTYPE_LABELS, OTHER_SUBTYPES } from '../lib/labels'
import { rosterFor, seatedKeys, toggleArchetype, withDefenseType, withSubtype } from '../lib/panel'
import { LITERATURE_KEY, LITERATURE_NOTE, shortTitle } from '../lib/panelCopy'
import { firstPreset, type PresetId, presetsFor, resolvedKeys } from '../lib/presets'
import { MAX_UPLOAD_MB } from '../lib/upload'
import { type Draft, type Notice, type QuotaView, quotaUsedUp } from '../state/flow'
import { NoticeBanner } from './NoticeBanner'
import { Button } from './ui'

const PANEL = limits.PanelConfig
// backend/settings.py's max_pages; the server's 413 stays the backstop, as for size.
export const MAX_PAGES = 250

type Props = {
  draft: Draft
  options: PanelOptions | null
  quota: QuotaView
  notice: Notice | null
  /** Null when the sample paper isn't shipped: the button is dropped (N5). */
  onTrySample: (() => void) | null
  onReadRecording: () => void
  onChange: (draft: Partial<Draft>) => void
  onSubmit: () => void
}

const fieldClass = 'mt-1 w-full rounded-lg border border-line-strong bg-field px-3 py-2 text-base focus:border-accent focus:outline-none'
const legendClass = 'pb-2.5 text-sm font-semibold text-muted'

export function StartScreen({ draft, options, quota, notice, onTrySample, onReadRecording, onChange, onSubmit }: Props) {
  const { panel } = draft
  const ids = { subtype: useId(), domain: useId(), topic: useId() }
  const blocked = quotaUsedUp(quota)
  // A new panel type selects its first offered preset: the Recommended row when it's valid there.
  const setPanelType = (next: Draft['panel']) => onChange({ panel: next, preset: firstPreset(options, next) })

  return (
    <div className="flex flex-wrap items-start gap-10">
      <section aria-label="About" className="flex min-w-0 flex-[1_1_380px] flex-col gap-5 sm:pt-3">
        <h2 className="font-serif text-[34px] leading-[1.1] font-semibold tracking-[-0.02em] sm:text-[44px]">
          Face a panel that has read your paper.
        </h2>
        <p className="max-w-[44ch] text-[17px] leading-relaxed text-muted">
          Upload your thesis or capstone. Each panelist questions you from your own pages, and after you answer you see the
          exact passage the question came from.
        </p>
        <div className="flex flex-col gap-2.5 pt-1.5">
          <span className="font-mono text-xs text-subtle">No paper handy?</span>
          <PathButton
            icon={<path d="M2 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H2zM22 4h-7a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h8z" />}
            title="Read a recorded defense"
            detail="A full session on our sample capstone. Free, no upload."
            onClick={onReadRecording}
          />
          {onTrySample && (
            <PathButton
              icon={<path d="M8 5v14l11-7z" />}
              title="Try it with the sample paper"
              detail="A live panel on the same capstone. Uses one of today's defenses."
              onClick={onTrySample}
              disabled={blocked}
            />
          )}
        </div>
      </section>

      <form
        aria-label="Start a defense"
        className="flex min-w-0 flex-[1_1_560px] flex-col gap-6 rounded-2xl border border-line-strong bg-card p-5 sm:p-7"
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit()
        }}
      >
        <DropZone file={draft.file} onFile={(file) => onChange({ file })} />

        <fieldset>
          <legend className={legendClass}>Defense type</legend>
          <div className="flex flex-wrap gap-2">
            {DEFENSE_TYPES.map((type) => (
              <Toggle key={type} pressed={panel.defense_type === type} onClick={() => setPanelType(withDefenseType(panel, type))}>
                {DEFENSE_TYPE_LABELS[type]}
              </Toggle>
            ))}
          </div>
          {panel.defense_type === 'other' && (
            <div className="mt-3">
              <label htmlFor={ids.subtype} className="block text-sm font-medium">
                Kind of panel
              </label>
              <select
                id={ids.subtype}
                className={fieldClass}
                value={panel.other_subtype ?? OTHER_SUBTYPES[0]}
                onChange={(event) => {
                  const subtype = OTHER_SUBTYPES.find((s) => s === event.target.value)
                  if (subtype) setPanelType(withSubtype(panel, subtype))
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
        </fieldset>

        <PanelPresets options={options} draft={draft} onChange={onChange} />

        <div className="flex flex-wrap items-end justify-between gap-4 border-t border-line pt-5">
          <fieldset>
            <legend className={legendClass}>Starting difficulty</legend>
            <div className="flex gap-1">
              {range(PANEL.starting_difficulty.minimum, PANEL.starting_difficulty.maximum).map((level) => (
                <Toggle
                  key={level}
                  square
                  pressed={panel.starting_difficulty === level}
                  onClick={() => onChange({ panel: { ...panel, starting_difficulty: level } })}
                >
                  {level}
                </Toggle>
              ))}
            </div>
          </fieldset>
          <Button type="submit" className="min-h-12 w-full sm:w-auto sm:px-7" disabled={draft.file === null || blocked}>
            Upload and convene the panel
          </Button>
        </div>
        <p className="-mt-3 text-sm text-muted">How hard the panel opens. It adapts from there based on your answers.</p>

        {notice && <NoticeBanner notice={notice} onRetry={notice.kind === 'network' || notice.kind === 'busy' ? onSubmit : undefined} />}

        <details className="group">
          <summary className="flex min-h-11 cursor-pointer list-none items-center gap-1.5 text-sm text-muted [&::-webkit-details-marker]:hidden">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="shrink-0 group-open:rotate-90">
              <path d="m9 6 6 6-6 6" />
            </svg>
            Optional details: domain and research title
          </summary>
          <div className="grid gap-4 pt-2 sm:grid-cols-2">
            <div>
              <label htmlFor={ids.domain} className="block text-sm font-medium">
                Domain / discipline
              </label>
              <input
                id={ids.domain}
                className={fieldClass}
                maxLength={PANEL.domain.maxLength}
                value={panel.domain ?? ''}
                onChange={(event) => onChange({ panel: { ...panel, domain: event.target.value || null } })}
              />
            </div>
            <div>
              <label htmlFor={ids.topic} className="block text-sm font-medium">
                Research title / topic
              </label>
              <input
                id={ids.topic}
                className={fieldClass}
                maxLength={PANEL.topic.maxLength}
                value={panel.topic ?? ''}
                onChange={(event) => onChange({ panel: { ...panel, topic: event.target.value || null } })}
              />
            </div>
            <p className="text-sm text-muted sm:col-span-2">Leave these blank and the panel reads them from your document.</p>
          </div>
        </details>
      </form>
    </div>
  )
}

function range(from: number, to: number): number[] {
  return Array.from({ length: to - from + 1 }, (_, i) => from + i)
}

function PathButton({ icon, title, detail, onClick, disabled = false }: { icon: ReactNode; title: string; detail: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex min-h-16 items-center gap-3.5 rounded-xl border border-line-strong bg-surface px-[18px] py-3.5 text-left transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-60 motion-reduce:transition-none"
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="shrink-0">
        {icon}
      </svg>
      <span className="flex flex-col gap-0.5">
        <span className="font-semibold">{title}</span>
        <span className="text-[13px] text-muted">{detail}</span>
      </span>
    </button>
  )
}

/** A toggle button (aria-pressed), as the canvas draws the type and difficulty choices. */
function Toggle({ pressed, onClick, square = false, children }: { pressed: boolean; onClick: () => void; square?: boolean; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`min-h-11 rounded-[10px] border font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent motion-reduce:transition-none ${
        square ? 'size-11 px-0' : 'px-[18px]'
      } ${pressed ? 'border-accent bg-accent-soft text-ink' : 'border-line-strong text-ink hover:border-accent'}`}
    >
      {children}
    </button>
  )
}

/** The PDF drop zone: drag a file on, or click to browse (the file input stays, for keyboards and phones). */
function DropZone({ file, onFile }: { file: File | null; onFile: (file: File | null) => void }) {
  const id = useId()
  const [over, setOver] = useState(false)
  const onDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault()
    setOver(false)
    const dropped = event.dataTransfer.files[0]
    if (dropped) onFile(dropped)
  }
  return (
    <label
      htmlFor={id}
      onDragOver={(event) => {
        event.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      className={`relative flex min-h-[150px] cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-[1.5px] border-dashed p-5 text-center transition-colors focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-accent motion-reduce:transition-none ${
        over ? 'border-accent bg-accent-soft' : 'border-devil-line bg-field hover:border-accent'
      }`}
    >
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="text-accent">
        <path d="M12 16V4" />
        <path d="m6 10 6-6 6 6" />
        <path d="M4 20h16" />
      </svg>
      {file ? (
        <>
          <span className="max-w-full truncate font-semibold">{file.name}</span>
          <span className="font-mono text-xs text-subtle">
            {(file.size / (1024 * 1024)).toFixed(1)} MB · drop another or <span className="text-accent underline underline-offset-[3px]">browse</span> to replace
          </span>
        </>
      ) : (
        <>
          <span className="font-semibold">
            Drop your PDF here, or <span className="text-accent underline underline-offset-[3px]">browse</span>
          </span>
          <span className="font-mono text-xs text-subtle">
            PDF with selectable text · up to {MAX_UPLOAD_MB} MB · {MAX_PAGES} pages
          </span>
        </>
      )}
      <input
        id={id}
        type="file"
        accept="application/pdf,.pdf"
        aria-label="Your document (PDF)"
        className="sr-only"
        onChange={(event) => onFile(event.target.files?.[0] ?? null)}
      />
    </label>
  )
}

function PanelPresets({ options, draft, onChange }: { options: PanelOptions | null; draft: Draft; onChange: (draft: Partial<Draft>) => void }) {
  const { panel, preset } = draft
  const roster = options && rosterFor(options, panel)
  if (options === null || roster === null) {
    return (
      <div>
        <p className={legendClass}>Panel</p>
        <p className="text-sm text-muted">Loading the panel presets. If you start before they load, you get the recommended one.</p>
      </div>
    )
  }
  const offered = presetsFor(options, panel)
  const titleOf = new Map(roster.archetypes.map((a) => [a.archetype_key, a]))
  const select = (id: PresetId) => {
    if (id === 'custom') {
      // Custom starts from the panel that was selected, so the boxes aren't empty.
      onChange({ preset: id, panel: { ...panel, archetype_keys: resolvedKeys(options, panel, preset) } })
    } else {
      onChange({ preset: id })
    }
  }
  // With no keys chosen (a type no preset fits), Custom shows the backend's recommended panel, which is what's sent.
  const customKeys = seatedKeys(options, panel)
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className={legendClass}>Panel</legend>
      {offered.map((p) => (
        <PresetRow
          key={p.id}
          pressed={preset === p.id}
          label={p.label}
          members={p.keys.map((k) => shortTitle(k, titleOf.get(k)?.title ?? k)).join(' · ')}
          onClick={() => select(p.id)}
        />
      ))}
      <PresetRow pressed={preset === 'custom'} label="Custom" members={`Choose up to ${options.max_domain_panelists} panelists yourself`} onClick={() => select('custom')} />
      {preset === 'custom' && (
        <div className="space-y-2 px-1 pt-2">
          <ul className="grid gap-x-4 gap-y-1 sm:grid-cols-2">
            {roster.archetypes.map((archetype) => {
              const checked = customKeys.includes(archetype.archetype_key)
              const full = !checked && customKeys.length >= options.max_domain_panelists
              return (
                <li key={archetype.archetype_key}>
                  <label className={`flex min-h-11 items-start gap-2.5 py-2 text-sm ${full ? 'opacity-50' : 'cursor-pointer'}`}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={full || (checked && customKeys.length === 1)}
                      onChange={() =>
                        onChange({ panel: { ...panel, archetype_keys: toggleArchetype(options, panel, archetype.archetype_key) } })
                      }
                      className="mt-0.5 size-[18px] shrink-0 accent-accent"
                    />
                    <span>
                      {archetype.title}
                      {archetype.archetype_key === LITERATURE_KEY && <span className="block text-xs text-muted">{LITERATURE_NOTE}</span>}
                    </span>
                  </label>
                </li>
              )
            })}
          </ul>
          <p className="font-mono text-xs text-subtle">Pick up to {options.max_domain_panelists} · the Devil's Advocate always joins</p>
        </div>
      )}
      <p className="pt-1 text-[13px] text-subtle">Every panel also seats a Devil's Advocate, who speaks last.</p>
    </fieldset>
  )
}

function PresetRow({ pressed, label, members, onClick }: { pressed: boolean; label: string; members: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`flex min-h-14 w-full items-center gap-3.5 rounded-xl border px-4 py-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent motion-reduce:transition-none ${
        pressed ? 'border-accent bg-accent-soft' : 'border-line-strong hover:border-accent'
      }`}
    >
      <span aria-hidden className={`size-[18px] shrink-0 rounded-full ${pressed ? 'border-[5px] border-accent' : 'border-[1.5px] border-subtle'}`} />
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="font-semibold">{label}</span>
        <span className="text-[13px] text-muted">{members}</span>
      </span>
    </button>
  )
}
