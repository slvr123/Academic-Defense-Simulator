// The answer box's draft, kept in localStorage per session and question (M10 N8), so a
// reload or a closed tab doesn't lose a half-written answer. Storage can throw (private
// windows, blocked site data, a full quota); every access is wrapped and fails silently,
// leaving the box working as it did before.
const PREFIX = 'ads.answer-draft'

function key(sessionId: string, turn: number): string {
  return `${PREFIX}:${sessionId}:${turn}`
}

export function loadAnswerDraft(sessionId: string, turn: number): string {
  try {
    return localStorage.getItem(key(sessionId, turn)) ?? ''
  } catch {
    return ''
  }
}

export function saveAnswerDraft(sessionId: string, turn: number, text: string): void {
  try {
    if (text === '') localStorage.removeItem(key(sessionId, turn))
    else localStorage.setItem(key(sessionId, turn), text)
  } catch {
    // Storage unavailable: the draft lives only in memory, as before N8.
  }
}

export function clearAnswerDraft(sessionId: string, turn: number): void {
  saveAnswerDraft(sessionId, turn, '')
}

/** Words as a reader counts them: runs of non-space characters. */
export function wordCount(text: string): number {
  const trimmed = text.trim()
  return trimmed === '' ? 0 : trimmed.split(/\s+/).length
}
