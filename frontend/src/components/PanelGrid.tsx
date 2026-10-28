// The panel (M10 N2, canvas Main.dc.html): four equal cards, 2×2 on phones. Each card
// has v1's portrait, the name and role on the left and the status on the right. Only
// the active card is lit (accent border, soft ground, dot); the Devil's Advocate's card
// is dashed. Replaces M8's centred bench row. Statuses use only what the client knows:
// the API never says who asks next, so there is no "Up next".
import { DEVILS_ADVOCATE_KEY } from '../lib/panel'
import { panelistName } from '../lib/labels'
import { shortTitle } from '../lib/panelCopy'
import { Avatar } from './Avatar'

export type Card = { archetypeKey: string; surname: string | null; title: string; icon: string }

/** What the lit panelist is doing. */
export type ActiveStatus = 'asking' | 'speaking' | 'reviewing'

const ACTIVE_TEXT: Record<ActiveStatus, string> = { asking: 'Asking', speaking: 'Speaking', reviewing: 'Reviewing' }

type Props = {
  cards: Card[]
  active: string | null
  status: ActiveStatus
  /** Questions each panelist has asked and had answered, by archetype key. */
  asked: Record<string, number>
}

function idleStatus(card: Card, asked: number): string {
  if (asked > 0) return `Asked ${asked}`
  return card.archetypeKey === DEVILS_ADVOCATE_KEY ? 'Speaks last' : 'Yet to ask'
}

export function PanelGrid({ cards, active, status, asked }: Props) {
  return (
    <section aria-label="The panel">
      <ul className="grid grid-cols-2 gap-2 sm:grid-cols-[repeat(auto-fit,minmax(240px,1fr))] sm:gap-3">
        {cards.map((card) => {
          const lit = card.archetypeKey === active
          const devil = card.archetypeKey === DEVILS_ADVOCATE_KEY
          const frame = lit ? 'border-solid border-accent bg-accent-soft' : devil ? 'border-dashed border-devil-line bg-surface' : 'border-solid border-line-strong bg-surface'
          return (
            <li
              key={card.archetypeKey}
              data-card={card.archetypeKey}
              data-lit={lit || undefined}
              aria-current={lit ? 'true' : undefined}
              className={`flex min-h-[72px] min-w-0 flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border px-3 py-2.5 transition-colors duration-500 motion-reduce:transition-none sm:flex-nowrap sm:px-4 sm:py-3 ${frame}`}
            >
              <Avatar icon={card.icon} size="md" dimmed={!lit} speaking={lit && status === 'speaking'} />
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-[15px] font-semibold text-ink">{card.surname ? panelistName(card.surname) : card.title}</span>
                <span className="truncate text-[12.5px] text-muted" title={card.title}>{shortTitle(card.archetypeKey, card.title)}</span>
              </span>
              <span className={`flex shrink-0 items-center gap-1.5 font-mono text-xs ${lit ? 'text-accent' : 'text-muted'}`}>
                {lit && <span aria-hidden className="size-[7px] rounded-full bg-accent" />}
                {lit ? (
                  status === 'reviewing' ? (
                    <span className="shimmer-text animate-shimmer motion-reduce:animate-none">{ACTIVE_TEXT.reviewing}</span>
                  ) : (
                    ACTIVE_TEXT[status]
                  )
                ) : (
                  idleStatus(card, asked[card.archetypeKey] ?? 0)
                )}
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
