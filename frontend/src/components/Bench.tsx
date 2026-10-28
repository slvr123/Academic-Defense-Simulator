// The bench (M8 K6): every panelist seated in a row, only the active one lit. The lit
// seat has the amber top bar, the glow and the highlighted name plate; the others are
// dimmed portraits. The Devil's Advocate's plate is dashed. Lighting is a CSS
// transition, so when the next panelist takes over the light moves across (K9).
import { DEVILS_ADVOCATE_KEY } from '../lib/panel'
import { panelistName } from '../lib/labels'
import { Avatar } from './Avatar'

export type Seat = { archetypeKey: string; surname: string | null; title: string; icon: string }

/** What the lit panelist is doing; `preparing` lights nobody and marks every seat as getting ready. */
export type BenchStatus = 'asking' | 'reviewing' | 'preparing'

type BenchProps = { seats: Seat[]; active: string | null; status: BenchStatus; speaking?: boolean }

const STATUS_TEXT: Record<BenchStatus, string> = { asking: 'Asking', reviewing: 'Reviewing', preparing: 'Preparing' }

function TopBar({ lit, preparing }: { lit: boolean; preparing: boolean }) {
  const color = lit ? 'bg-accent' : preparing ? 'bg-preparing' : 'bg-line'
  return <div aria-hidden className={`h-[3px] w-8 rounded-b-[3px] transition-colors duration-500 motion-reduce:transition-none sm:w-14 ${color}`} />
}

function Glow({ lit }: { lit: boolean }) {
  return (
    <div
      aria-hidden
      className={`pointer-events-none absolute top-0 left-1/2 h-[76px] w-[110px] -translate-x-1/2 bg-[radial-gradient(ellipse_50%_85%_at_50%_0%,color-mix(in_srgb,var(--color-accent)_22%,transparent),transparent)] transition-opacity duration-700 motion-reduce:transition-none sm:h-[150px] sm:w-[240px] ${lit ? 'opacity-100' : 'opacity-0'}`}
    />
  )
}

function Plate({ seat, lit, status }: { seat: Seat; lit: boolean; status: BenchStatus }) {
  const devil = seat.archetypeKey === DEVILS_ADVOCATE_KEY
  const frame = lit ? 'border-accent bg-accent-soft' : devil ? 'border-dashed border-devil-line' : 'border-transparent'
  const name = seat.surname ? panelistName(seat.surname) : seat.title
  let detail
  if (lit && status === 'reviewing') {
    detail = <span className="shimmer-text animate-shimmer font-mono text-xs motion-reduce:animate-none">{STATUS_TEXT.reviewing}</span>
  } else if (status === 'preparing') {
    detail = <span className="font-mono text-xs text-preparing">{STATUS_TEXT.preparing}</span>
  } else {
    detail = <span className={`text-xs ${lit ? 'text-muted' : 'text-subtle'}`}>{seat.title}</span>
  }
  return (
    <div
      className={`flex min-w-0 flex-col items-center gap-0.5 rounded-lg border px-2 py-1 text-center transition-colors duration-500 motion-reduce:transition-none sm:px-4 sm:py-[7px] ${frame}`}
    >
      <span className={`flex max-w-full items-center gap-1.5 text-xs font-semibold sm:text-sm ${lit ? 'text-ink' : 'text-ink-dim'}`}>
        {lit && <span aria-hidden className="hidden size-[7px] shrink-0 rounded-full bg-accent sm:inline-block" />}
        {/* Phones show surnames only, as bench-mobile does. */}
        <span className="truncate sm:hidden">{seat.surname ?? seat.title}</span>
        <span className="hidden truncate sm:inline">{name}</span>
      </span>
      <span className="hidden max-w-full truncate sm:block">{detail}</span>
    </div>
  )
}

export function Bench({ seats, active, status, speaking = false }: BenchProps) {
  return (
    <section aria-label="The panel" className="overflow-hidden border-b border-line bg-surface sm:rounded-2xl sm:border">
      <ul className="flex items-end px-2 sm:px-3">
        {seats.map((seat) => {
          const lit = seat.archetypeKey === active
          return (
            <li
              key={seat.archetypeKey}
              aria-current={lit ? 'true' : undefined}
              className="relative flex min-w-0 flex-1 basis-0 flex-col items-center"
            >
              <TopBar lit={lit} preparing={status === 'preparing'} />
              <Glow lit={lit} />
              <div className="relative mt-3 mb-4 sm:mt-5 sm:mb-6">
                <Avatar icon={seat.icon} size="bench" dimmed={!lit} speaking={lit && speaking} />
              </div>
              {lit && <span className="sr-only">{status === 'reviewing' ? ', reviewing your answer' : speaking ? ', speaking' : ', asking'}</span>}
            </li>
          )
        })}
      </ul>
      <div className="flex border-t-2 border-rail bg-raised px-2 pt-2 pb-2.5 sm:px-3 sm:pt-3 sm:pb-3.5">
        {seats.map((seat) => (
          <div key={seat.archetypeKey} className="flex min-w-0 flex-1 basis-0 justify-center px-0.5 sm:px-1.5">
            <Plate seat={seat} lit={seat.archetypeKey === active} status={status} />
          </div>
        ))}
      </div>
    </section>
  )
}
