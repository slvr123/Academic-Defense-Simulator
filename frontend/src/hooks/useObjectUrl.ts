import { useEffect, useState } from 'react'

export type ObjectUrl = {
  /** The URL, once made for the current file; null before the first open or without a file. */
  url: string | null
  /** Whether there is a file to open. False after a reload, when the File is gone (N4). */
  available: boolean
  /** Makes the URL on first use, from the click that opens the PDF. */
  ensure: () => void
}

/**
 * An object URL for the candidate's own PDF (M10 N4): the browser's viewer reads the file
 * from memory, so nothing is uploaded again. Made on the first open and kept for the
 * session; revoked when `file` changes or goes (the session ended), and on pagehide, so
 * the Blob isn't held past the page.
 */
export function useObjectUrl(file: File | null): ObjectUrl {
  const [made, setMade] = useState<{ file: File; url: string } | null>(null)
  const current = made !== null && made.file === file ? made : null
  const available = file !== null && typeof URL.createObjectURL === 'function'

  useEffect(() => {
    if (current === null) return
    const revoke = () => URL.revokeObjectURL(current.url)
    window.addEventListener('pagehide', revoke)
    return () => {
      window.removeEventListener('pagehide', revoke)
      revoke()
    }
  }, [current])

  return {
    url: current?.url ?? null,
    available,
    ensure: () => {
      if (current === null && file !== null && available) setMade({ file, url: URL.createObjectURL(file) })
    },
  }
}
