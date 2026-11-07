// "N of 11 defenses left today" (D15). Hidden while loading and when /quota can't
// answer (503: Neon unreachable); the server still enforces the cap. With the visitor's
// own Gemini key (M17), the free count doesn't apply, and it says so.
import { moreAt } from '../lib/manilaTime'
import type { QuotaView } from '../state/flow'

export const OWN_KEY_QUOTA_TEXT = 'Using your own key'

export function QuotaLine({ quota, ownKey = false }: { quota: QuotaView; ownKey?: boolean }) {
  if (ownKey) return <p className="text-sm text-muted">{OWN_KEY_QUOTA_TEXT}</p>
  if (quota.kind !== 'known') return null
  const { remaining, cap, resets_at } = quota.quota
  if (remaining === 0) {
    return <p className="text-sm text-danger">No defenses left today. {moreAt(new Date(resets_at))}</p>
  }
  return (
    <p className="text-sm text-muted">
      {remaining} of {cap} defenses left today
    </p>
  )
}
