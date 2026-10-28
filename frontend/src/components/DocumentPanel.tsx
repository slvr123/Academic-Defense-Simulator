// The candidate's own PDF (M10 N4): a side panel on wide screens and a full-screen sheet
// on phones, showing the file through the browser's built-in viewer at #page=N. The URL
// is an object URL of the uploaded File, so nothing is uploaded again. Only a close
// control: the built-in viewer has its own page controls, and prev/next from outside it
// would need a reload per page, which some viewers ignore (#page= on mobile browsers).
import { useRef } from 'react'
import { useFocusTrap } from '../hooks/useFocusTrap'
import { useMediaQuery } from '../hooks/useMediaQuery'

export function DocumentPanel({ url, page, onClose }: { url: string; page: number; onClose: () => void }) {
  const root = useRef<HTMLElement>(null)
  // The phone sheet covers the page, so it traps focus; on wide screens the panel sits
  // beside the question and Tab moves freely between them.
  const sheet = !useMediaQuery('(min-width: 1024px)')
  useFocusTrap(root, onClose, sheet)
  return (
    <section
      ref={root}
      aria-label="Your PDF"
      className="fixed inset-0 z-40 flex flex-col gap-3 bg-ground p-3 lg:sticky lg:top-4 lg:z-auto lg:h-[calc(100dvh-2rem)] lg:min-w-0 lg:flex-[1_1_420px] lg:rounded-2xl lg:border lg:border-line-strong lg:bg-card lg:p-[18px]"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold">
          Your PDF <span className="font-mono text-xs font-normal text-muted">· opened at page {page}</span>
        </span>
        <button
          type="button"
          aria-label="Close PDF"
          onClick={onClose}
          className="flex size-11 items-center justify-center rounded-lg text-muted transition-colors hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent motion-reduce:transition-none"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </div>
      {/* Keyed by page: a new #page on the same document isn't always honoured, a fresh frame is. */}
      <iframe key={page} src={`${url}#page=${page}`} title={`Your PDF, page ${page}`} className="min-h-0 w-full flex-1 rounded border border-line-strong bg-paper" />
      <p className="font-mono text-xs text-subtle">Opened from your own file in this browser. Nothing is uploaded again.</p>
    </section>
  )
}
