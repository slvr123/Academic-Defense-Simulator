// Limits reset at midnight Pacific; the people using this read Manila time (D16).
const FORMAT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Manila',
  weekday: 'short',
  hour: 'numeric',
  minute: '2-digit',
})

/** e.g. "Thu 3:00 PM" */
export function formatManila(when: Date): string {
  return FORMAT.format(when)
}

export function afterSeconds(now: Date, seconds: number): Date {
  return new Date(now.getTime() + seconds * 1000)
}

/** "More at Thu 3:00 PM, Manila time." */
export function moreAt(when: Date): string {
  return `More at ${formatManila(when)}, Manila time.`
}
