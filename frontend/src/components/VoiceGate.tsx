// How the panel speaks, chosen before the first question (M3b E9). Panel voices send
// question text to Xiaomi, so they need a choice made knowingly, and are only offered
// when the server has a MiMo key (E6). The wording is v1.2.2's disclosure, with the
// Streamlit toggle turned into one of three choices.
import { useId } from 'react'
import type { VoiceChoice } from '../state/flow'

type Option = { value: VoiceChoice; label: string; detail: string }

/** The M3b disclosure, also shown before the header menu's first switch to Panel voice (M10 N9). */
export const PANEL_VOICE_DISCLOSURE =
  "Higher-quality AI voices send each question's text, which is drawn from your document, to Xiaomi's Mimo API for speech synthesis (slower, about 7–15 seconds per question). Falls back to your browser's voice automatically if it fails."

const PANEL: Option = { value: 'panel', label: 'Panel voices', detail: PANEL_VOICE_DISCLOSURE }
const BROWSER: Option = { value: 'browser', label: 'Browser voice', detail: "Your browser's own speech engine reads each question." }
const NONE: Option = { value: 'none', label: 'No voice', detail: 'Questions appear as text only.' }

export function VoiceGate({ voice, ttsAvailable, onChoose }: { voice: VoiceChoice | null; ttsAvailable: boolean; onChoose: (voice: VoiceChoice) => void }) {
  const name = useId()
  const options = ttsAvailable ? [PANEL, BROWSER, NONE] : [BROWSER, NONE]
  return (
    <fieldset className="space-y-2">
      <legend className="mb-2 text-sm font-medium">How should the panel speak?</legend>
      {options.map((option) => (
        <label
          key={option.value}
          className={`flex cursor-pointer gap-3 rounded-lg border p-3 text-sm ${voice === option.value ? 'border-accent bg-accent-soft' : 'border-line bg-card'}`}
        >
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={voice === option.value}
            onChange={() => onChoose(option.value)}
            className="mt-0.5 accent-accent"
          />
          <span>
            <span className="block font-medium">{option.label}</span>
            <span className="block text-muted">{option.detail}</span>
          </span>
        </label>
      ))}
      <p className="text-xs text-muted">This choice is not saved between sessions.</p>
    </fieldset>
  )
}
