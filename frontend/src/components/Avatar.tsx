// A panelist's portrait from the static site's /icons (D11), ringed in its archetype accent.
import { accentFor } from '../lib/accents'

const SIZES = { sm: 'size-8', md: 'size-12', lg: 'size-16' } as const

export function Avatar({ icon, archetypeKey, size = 'md' }: { icon: string; archetypeKey: string; size?: keyof typeof SIZES }) {
  return (
    <img
      src={`/icons/${icon}.png`}
      alt=""
      className={`${SIZES[size]} shrink-0 rounded-full border-2 bg-paper object-cover ${accentFor(archetypeKey).border}`}
    />
  )
}
