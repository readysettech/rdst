import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { requestSetupGuide, useSetupGuideState } from './setupGuideStore'
import { hasSetupSignal, isSetupComplete } from './setupModel'
import { useSetupProgress } from './useSetupProgress'

/**
 * The recovery path for a dismissed setup guide (C4): a quiet sidebar utility
 * next to Docs, present only while the guide was dismissed AND steps remain.
 * Dismissal stays permanent — nothing re-surfaces the guide except this click.
 */
export function SetupGuideHelpEntry({ className }: { className: string }) {
  const { dismissed } = useSetupGuideState()
  const { data } = useSetupProgress()

  if (!dismissed || !hasSetupSignal(data) || isSetupComplete(data)) return null

  return (
    <Pressable
      type="button"
      onClick={requestSetupGuide}
      data-testid="setup-guide-help-entry"
      className={className}
    >
      <Icon
        name="road-wayside"
        label="Setup guide"
        className="w-4 h-4 text-content-layout-3 group-hover:scale-110 transition-transform"
      />
      <span>Setup guide</span>
    </Pressable>
  )
}
