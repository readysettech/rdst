import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m } from '@rs/ui-new/motion'
import { HStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useEffect, useState } from 'react'
import { trackEvent } from '../lib/analytics'
import {
  nudgeDue,
  SESSION_DELAY_MS,
  silenceNudge,
  snoozeNudge,
} from '../lib/feedbackNudge'
import { FeedbackChat } from './FeedbackChat'

const MODAL_RETRY_MS = 30_000

/**
 * The occasional corner invitation to the Feedback & help chat. Never modal;
 * see lib/feedbackNudge for when it shows and how long it stays away.
 */
export function FeedbackNudge() {
  const [visible, setVisible] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>
    const show = () => {
      if (!nudgeDue()) return
      // Wait out an open modal (sign-in, setup) so the nudge never competes with it.
      if (document.querySelector('[aria-modal="true"]')) {
        timer = setTimeout(show, MODAL_RETRY_MS)
        return
      }
      snoozeNudge('shown')
      trackEvent('feedback_nudge_shown')
      setVisible(true)
    }
    timer = setTimeout(show, SESSION_DELAY_MS)
    return () => clearTimeout(timer)
  }, [])

  const dismiss = (forever: boolean) => {
    if (forever) silenceNudge()
    else snoozeNudge('dismissed')
    trackEvent('feedback_nudge_dismissed', { forever })
    setVisible(false)
  }

  return (
    <>
      <FeedbackChat isOpen={chatOpen} onClose={() => setChatOpen(false)} />
      <AnimatePresence>
        {visible && (
          <m.div
            role="dialog"
            aria-label="Feedback invitation"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
            className="fixed bottom-4 right-4 z-50 w-[min(360px,calc(100vw-2rem))] rounded-card border border-border-layout-2 bg-surface-overlay p-4 shadow-elevation-2"
          >
            <HStack className="items-start gap-3">
              <Icon
                name="customer-support"
                label=""
                className="mt-0.5 h-5 w-5 shrink-0 text-content-primary-soft"
              />
              <div className="min-w-0 flex-1">
                <Text level="label-small" className="text-content-layout-1">
                  What would make RDST better for you?
                </Text>
                <Text level="body-small" className="mt-1 text-content-layout-3">
                  Tell us what to add or fix.
                </Text>
                <HStack className="mt-3 items-center gap-2">
                  <Button
                    size="small"
                    variant="primary"
                    modifier="solid"
                    label="Tell us"
                    onClick={() => {
                      trackEvent('feedback_nudge_clicked')
                      setVisible(false)
                      setChatOpen(true)
                    }}
                  />
                  <Button
                    size="small"
                    variant="primary"
                    modifier="ghost"
                    label="Not now"
                    onClick={() => dismiss(false)}
                  />
                  <Button
                    size="small"
                    variant="primary"
                    modifier="link"
                    label="Don't ask again"
                    classMerge="ml-auto h-auto p-0"
                    onClick={() => dismiss(true)}
                  />
                </HStack>
              </div>
            </HStack>
          </m.div>
        )}
      </AnimatePresence>
    </>
  )
}
