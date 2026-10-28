// The session header (M10 N2, canvas Main.dc.html), spread across the width: the
// wordmark and "title · type" on the left, the question and its progress dots in the
// middle, and the voice menu, "Your document" and "End session" on the right. The API
// sends no total number of questions (follow-ups vary it), so there's no "of Y".
import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from 'react'
import { useFocusTrap } from '../hooks/useFocusTrap'
import type { VoiceChoice } from '../state/flow'
import { Button, DocumentIcon } from './ui'
import { PANEL_VOICE_DISCLOSURE } from './VoiceGate'

export const WORDMARK = 'Academic Defense Simulator'

type Props = {
  subtitle: string
  turn: number
  /** A label beside the wordmark, e.g. "Recorded demo" (N5). */
  badge?: ReactNode
  voice: VoiceChoice | null
  ttsAvailable: boolean
  panelDisclosed: boolean
  onChooseVoice: (voice: VoiceChoice) => void
  /** Null when the candidate's file isn't available (after a reload): the button is hidden (N4). */
  onOpenDocument: (() => void) | null
  documentOpen: boolean
  endLabel?: string
  onEnd: () => void
}

export function SessionHeader({ subtitle, turn, badge, voice, ttsAvailable, panelDisclosed, onChooseVoice, onOpenDocument, documentOpen, endLabel = 'End session', onEnd }: Props) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="font-serif text-lg font-semibold tracking-[-0.01em] sm:text-xl">{WORDMARK}</h1>
          {badge}
        </span>
        <p className="min-w-0 truncate font-mono text-xs text-muted">{subtitle}</p>
      </div>
      <div className="flex items-center gap-3 font-mono text-xs text-muted">
        <span>Question {turn}</span>
        <span aria-hidden className="flex flex-wrap gap-[5px]">
          {Array.from({ length: turn }, (_, i) => (
            <span key={i} className={`h-1 w-[18px] rounded-sm ${i + 1 === turn ? 'bg-accent' : 'bg-ink'}`} />
          ))}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <VoiceMenu voice={voice} ttsAvailable={ttsAvailable} panelDisclosed={panelDisclosed} onChoose={onChooseVoice} />
        {onOpenDocument && (
          <button
            type="button"
            aria-pressed={documentOpen}
            onClick={onOpenDocument}
            className={`flex min-h-11 items-center gap-2 rounded-[10px] border px-3.5 text-sm transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent motion-reduce:transition-none ${
              documentOpen ? 'border-accent bg-accent-soft' : 'border-line-strong bg-card'
            }`}
          >
            <DocumentIcon size={16} /> Your document
          </button>
        )}
        <EndSession label={endLabel} onEnd={onEnd} />
      </div>
    </div>
  )
}

const VOICE_LABELS: Record<VoiceChoice, string> = { panel: 'Panel voice', browser: 'Browser voice', none: 'No voice' }

function SpeakerIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M11 5 6 9H3v6h3l5 4z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
      <path d="M18.5 5.5a9 9 0 0 1 0 13" />
    </svg>
  )
}

/** Panel voice / Browser voice / No voice, switchable mid-session (N9). The first switch
 * to Panel voice in a session shows the M3b disclosure first, with a confirm. */
function VoiceMenu({ voice, ttsAvailable, panelDisclosed, onChoose }: { voice: VoiceChoice | null; ttsAvailable: boolean; panelDisclosed: boolean; onChoose: (voice: VoiceChoice) => void }) {
  const [open, setOpen] = useState(false)
  const [disclosing, setDisclosing] = useState(false)
  const menuId = useId()
  const root = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const current = voice ?? 'none'
  const choices: VoiceChoice[] = ttsAvailable ? ['panel', 'browser', 'none'] : ['browser', 'none']

  const close = useCallback(() => {
    setOpen(false)
    setDisclosing(false)
    button.current?.focus()
  }, [])

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onPointer)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onPointer)
    }
  }, [open, close])

  const choose = (next: VoiceChoice) => {
    if (next === 'panel' && !panelDisclosed) {
      setDisclosing(true)
      return
    }
    onChoose(next)
    close()
  }

  return (
    <div ref={root} className="relative">
      <button
        ref={button}
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={`Voice: ${VOICE_LABELS[current]}. Change voice`}
        onClick={() => (open ? close() : setOpen(true))}
        className="flex min-h-11 items-center gap-2 rounded-[10px] border border-line-strong bg-card px-3.5 text-sm transition-colors hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent motion-reduce:transition-none"
      >
        <SpeakerIcon /> {VOICE_LABELS[current]}
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div id={menuId} className="absolute top-full right-0 z-30 mt-2 w-[min(320px,calc(100vw-32px))] rounded-xl border border-line-strong bg-raised p-2 shadow-[0_18px_40px_rgba(0,0,0,0.45)] max-sm:left-0">
          {disclosing ? (
            <div className="space-y-3 p-2 text-sm">
              <p className="font-semibold">Use panel voices?</p>
              <p className="text-muted">{PANEL_VOICE_DISCLOSURE}</p>
              <div className="flex flex-wrap gap-2">
                <Button
                  onClick={() => {
                    onChoose('panel')
                    close()
                  }}
                >
                  Use panel voices
                </Button>
                <Button variant="secondary" onClick={() => setDisclosing(false)}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <fieldset>
              <legend className="sr-only">Voice</legend>
              {choices.map((choice) => (
                <label key={choice} className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-3 text-sm hover:bg-accent-soft">
                  <input type="radio" name={menuId} checked={current === choice} onChange={() => choose(choice)} className="accent-accent" />
                  {VOICE_LABELS[choice]}
                </label>
              ))}
            </fieldset>
          )}
        </div>
      )}
    </div>
  )
}

/** "End session": an in-app confirm, never window.confirm (N2). */
function EndSession({ label, onEnd }: { label: string; onEnd: () => void }) {
  const [confirming, setConfirming] = useState(false)
  return (
    <>
      <button
        type="button"
        onClick={() => setConfirming(true)}
        className="min-h-11 rounded-[10px] border border-transparent px-3 text-sm text-muted transition-colors hover:border-line-strong hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent motion-reduce:transition-none"
      >
        {label}
      </button>
      {confirming && <ConfirmEnd label={label} onCancel={() => setConfirming(false)} onConfirm={onEnd} />}
    </>
  )
}

function ConfirmEnd({ label, onCancel, onConfirm }: { label: string; onCancel: () => void; onConfirm: () => void }) {
  const box = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const [ending, setEnding] = useState(false)
  useFocusTrap(box, onCancel)
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ground/80 p-4">
      <div ref={box} role="alertdialog" aria-modal="true" aria-labelledby={titleId} className="w-full max-w-md space-y-4 rounded-2xl border border-line-strong bg-card p-6">
        <h2 id={titleId} className="font-serif text-xl font-semibold">
          {label}?
        </h2>
        <p className="text-sm text-muted">The panel stops here and this session is closed. You can start a new defense from the start screen.</p>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={onCancel}>
            Keep going
          </Button>
          <Button
            disabled={ending}
            onClick={() => {
              setEnding(true)
              onConfirm()
            }}
          >
            {label}
          </Button>
        </div>
      </div>
    </div>
  )
}
