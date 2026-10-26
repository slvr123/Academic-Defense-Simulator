// A panelist's portrait from the static site's /icons (D11), ringed in its archetype accent.
import { accentFor } from '../lib/accents'

const SIZES = { sm: 'size-8', md: 'size-12', lg: 'size-16' } as const

// Speaking: a pulsing ring, or a still one when motion is reduced (E11).
const SPEAKING = 'animate-speaking motion-reduce:animate-none motion-reduce:ring-4 motion-reduce:ring-brand/40'

type AvatarProps = { icon: string; archetypeKey: string; size?: keyof typeof SIZES; speaking?: boolean }

export function Avatar({ icon, archetypeKey, size = 'md', speaking = false }: AvatarProps) {
  return (
    <img
      src={`/icons/${icon}.png`}
      alt=""
      data-speaking={speaking || undefined}
      className={`${SIZES[size]} shrink-0 rounded-full border-2 bg-paper object-cover ${accentFor(archetypeKey).border} ${speaking ? SPEAKING : ''}`}
    />
  )
}
