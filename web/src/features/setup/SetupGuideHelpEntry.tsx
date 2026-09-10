import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { trackEvent } from '../../lib/analytics'
import { requestSetupGuide, useSetupGuideState } from './setupGuideStore'
import { hasSetupSignal, isSetupComplete } from './setupModel'
import { useSetupProgress } from './useSetupProgress'

/**
 * The recovery path for a hidden setup guide (C4): a quiet sidebar utility
 * next to Docs, present only while the guide was hidden AND steps remain.
 * Hiding stays permanent — nothing brings the checklist back except this click.
 */
export function SetupGuideHelpEntry({ className }: { className: string }) {
  const { dismissed } = useSetupGuideState()
  const { data } = useSetupProgress()

  if (!dismissed || !hasSetupSignal(data) || isSetupComplete(data)) return null

  return (
    <Pressable
      type="button"
      onClick={() => {
        requestSetupGuide()
        trackEvent('setup_guide_opened')
      }}
      data-testid="setup-guide-help-entry"
      className={className}
    >
      <Icon
        name="road-wayside"
        label=""
        aria-hidden="true"
        className="w-4 h-4 text-content-layout-3 group-hover:scale-110 transition-transform"
      />
      <span>Show setup guide</span>
    </Pressable>
  )
}
