import { cn } from '@rs/tailwind-base'
import { Pressable } from '@rs/ui-new/pressable'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import {
  HAND_RAISER_URL,
  isHandRaiserSeen,
  markHandRaiserSeen,
  type PqlSignal,
} from '../lib/handRaiser'

type Tone = 'positive' | 'info' | 'accent'

interface HandRaiserProps {
  signal: PqlSignal
  /**
   * The invitation, composed with real evidence by the caller; the honesty
   * contract is to name what triggered it ("14 instances", "a month on imdb").
   */
  message: string
  linkLabel?: string
  tone?: Tone
  /** Offer an explicit "Don't show again" alongside the once-ever behavior. */
  showDismiss?: boolean
}

const accentBorder: Record<Tone, string> = {
  positive: 'border-l-border-positive-solid',
  info: 'border-l-border-info-solid',
  accent: 'border-l-border-primary-solid',
}

/**
 * A quiet product-led-sales invitation (rdst-dma.4). Rides inside the result it
 * celebrates, never modal, never before the user's success is on screen. Shows
 * at most once per signal, ever (marked on first display); an optional "Don't
 * show again" hides it immediately. Copy: design/proposals/pql-moments.html.
 */
export function HandRaiser({
  signal,
  message,
  linkLabel = 'Talk to an engineer',
  tone = 'positive',
  showDismiss,
}: HandRaiserProps) {
  const [visible, setVisible] = useState(() => !isHandRaiserSeen(signal))

  // Once per signal, ever: mark on first display so it never returns, even on
  // a persistent surface like the Home footer.
  useEffect(() => {
    if (visible) markHandRaiserSeen(signal)
  }, [visible, signal])

  if (!visible) return null

  return (
    <div
      className={cn(
        'rounded-lg border border-border-layout-1 border-l-4 bg-surface-raised px-4 py-3',
        accentBorder[tone],
      )}
    >
      <VStack className="gap-1.5 items-start">
        <Text level="body-small" className="text-content-layout-2">
          {message}
        </Text>
        <HStack className="gap-3 items-center flex-wrap">
          <a
            href={HAND_RAISER_URL}
            target="_blank"
            rel="noreferrer"
            className="text-label-small text-content-primary-soft hover:underline"
          >
            {linkLabel} &rarr;
          </a>
          {showDismiss && (
            // Kept as a hand-roll: a quiet muted-text tertiary dismiss paired
            // beside the primary invitation link; a link-modifier Button (primary
            // color + underline) would break that quiet pairing.
            <Pressable
              type="button"
              onClick={() => setVisible(false)}
              className="text-label-small text-content-layout-3 hover:text-content-layout-1"
            >
              Don't show again
            </Pressable>
          )}
        </HStack>
      </VStack>
    </div>
  )
}
