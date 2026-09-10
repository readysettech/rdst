import type { IconStrokeName } from '@rs/ui-icons/icon-name'

/**
 * The tones a benchmark outcome can carry. `neutral` is the tone for an
 * outcome with no verdict in it -- queued work, a run the user stopped --
 * so those stop borrowing warning-amber (GUIDELINES section 4).
 */
export type StatusTone =
  | 'positive'
  | 'warning'
  | 'negative'
  | 'informative'
  | 'neutral'

/**
 * Status is colour + icon + label, never colour alone (GUIDELINES section 4),
 * and the pairing is fixed so the same tone reads the same way on the verdict
 * band, a query card and a load-test header.
 */
const TONE_ICON: Record<StatusTone, IconStrokeName> = {
  positive: 'tick-double',
  warning: 'alert',
  negative: 'close',
  informative: 'play',
  neutral: 'minus',
}

export function statusToneIcon(tone: StatusTone): IconStrokeName {
  return TONE_ICON[tone]
}
