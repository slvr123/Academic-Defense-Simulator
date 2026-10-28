// A panelist's portrait: v1's PNG from the static site's /icons (D11). The Bench design
// lights only the active panelist (M8 K6); everyone else is dimmed, not replaced.
const SIZES = { sm: 'size-8', md: 'size-12', lg: 'size-16', bench: 'size-11 sm:size-[72px]' } as const

// Speaking: a breathing amber ring, or a still one when motion is reduced (K9).
const SPEAKING = 'animate-speaking motion-reduce:animate-none motion-reduce:ring-4 motion-reduce:ring-accent/40'

type AvatarProps = { icon: string; size?: keyof typeof SIZES; dimmed?: boolean; speaking?: boolean }

export function Avatar({ icon, size = 'md', dimmed = false, speaking = false }: AvatarProps) {
  return (
    <img
      src={`/icons/${icon}.png`}
      alt=""
      data-speaking={speaking || undefined}
      data-dimmed={dimmed || undefined}
      className={`${SIZES[size]} shrink-0 rounded-full border-2 bg-field object-cover transition-[filter,opacity,border-color] duration-500 motion-reduce:transition-none ${
        dimmed ? 'border-line opacity-45 grayscale' : 'border-accent'
      } ${speaking ? SPEAKING : ''}`}
    />
  )
}
