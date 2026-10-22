// "N of 11 defenses left today" (D15). Hidden while loading and when /quota can't
// answer (503: Neon unreachable); the server still enforces the cap.
import { moreAt } from '../lib/manilaTime'
import type { QuotaView } from '../state/flow'

export function quotaUsedUp(quota: QuotaView): boolean {
  return quota.kind === 'known' && quota.quota.remaining === 0
}

export function QuotaLine({ quota }: { quota: QuotaView }) {
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
