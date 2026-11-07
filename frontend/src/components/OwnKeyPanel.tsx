// M17: "Use your own Gemini key" on the start screen. The header's link opens it; when
// today's free defenses are used up it's open on its own, as the main way on. The key is
// checked with the server (POST /keys/check) and then held by useFlow for the next upload
// only. The input forgets it once Google accepts it; nothing is written to storage or the URL.
import { useId, useState } from 'react'
import type { ApiResult } from '../api/client'
import { Button } from './ui'

export const KEY_HELP_URL = 'https://aistudio.google.com/apikey'
export const USED_UP_LEAD = "Today's free defenses are used. Use your own free Gemini key to keep practicing."
export const KEY_READY_TEXT = "Using your own Gemini key. Your next defense runs on it and doesn't use a free one."

type Props = {
  /** A key Google accepted is waiting for the next upload. */
  active: boolean
  /** Today's free defenses are used up: the panel leads with that. */
  prominent: boolean
  onSubmit: (key: string) => Promise<ApiResult<unknown>>
  onRemove: () => void
}

export function OwnKeyPanel({ active, prominent, onSubmit, onRemove }: Props) {
  const id = useId()
  const [value, setValue] = useState('')
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (active) {
    return (
      <section aria-label="Your own Gemini key" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line-strong bg-card px-4 py-3 text-sm">
        <p>{KEY_READY_TEXT}</p>
        <Button variant="secondary" onClick={onRemove}>
          Remove key
        </Button>
      </section>
    )
  }

  const submit = async () => {
    const key = value.trim()
    if (key === '' || checking) return
    setChecking(true)
    setError(null)
    const result = await onSubmit(key)
    setChecking(false)
    if (result.ok) {
      setValue('')
      return
    }
    const { error: failure } = result
    setError(failure.kind === 'network' ? "Can't reach the server. Try again." : failure.message)
  }

  return (
    <section
      id="own-key"
      aria-label="Use your own Gemini key"
      className={`rounded-xl border px-4 py-4 sm:px-5 ${prominent ? 'border-action bg-action-soft' : 'border-line-strong bg-card'}`}
    >
      {prominent && <p className="mb-3 font-semibold">{USED_UP_LEAD}</p>}
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <div className="min-w-0 flex-[1_1_260px]">
          <label htmlFor={id} className="block text-sm font-medium">
            Your Gemini API key
          </label>
          <input
            id={id}
            type="password"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            value={value}
            onChange={(event) => {
              setValue(event.target.value)
              setError(null)
            }}
            aria-invalid={error !== null}
            aria-describedby={`${id}-help`}
            className="mt-1 w-full rounded-lg border border-line-strong bg-field px-3 py-2 font-mono text-base focus:border-action focus:outline-none"
          />
        </div>
        <Button type="submit" disabled={value.trim() === '' || checking} className="w-full sm:w-auto">
          {checking ? 'Checking...' : 'Use this key'}
        </Button>
      </form>
      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}
      <p id={`${id}-help`} className="mt-2 text-sm text-muted">
        Free, about 60 seconds:{' '}
        <a href={KEY_HELP_URL} target="_blank" rel="noopener noreferrer" className="text-action-text underline underline-offset-[3px]">
          aistudio.google.com/apikey
        </a>
        . Sign in with Google, then Create API key. No card needed.
      </p>
    </section>
  )
}
