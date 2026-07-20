import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { HStack, VStack } from '@rs/ui-new/stack';
import { useTrialSource } from '../lib/trialQueries';

export function TrialBalanceBadge() {
  const { isTrialSource, trialStatus } = useTrialSource();

  // Only show the sidebar badge for the current trial-backed credential source.
  // Cached trial-status data must stay hidden once the active source changes away
  // from the trial, but an exhausted trial should still render as trial-backed state.
  if (!isTrialSource) return null;

  const isExhaustedStatus = trialStatus?.status === 'exhausted';
  if (!trialStatus || (!trialStatus.active && !isExhaustedStatus)) return null;
  const { remaining_tokens_display, limit_tokens_display, percent_remaining } = trialStatus;

  if (!remaining_tokens_display || !limit_tokens_display || percent_remaining == null) {
    return (
      <div className="rounded-lg bg-surface-primary-soft/20 px-3 py-2">
        <HStack className="gap-2 items-center">
          <Icon name="sparkles" label="Trial" className="w-3.5 h-3.5 text-content-primary-soft" />
          <Text level="caption" className="text-content-primary-soft">
            Trial active
          </Text>
        </HStack>
      </div>
    );
  }

  const isLow = percent_remaining < 25;
  const isExhausted = percent_remaining <= 0;

  const bgClass = isExhausted
    ? 'bg-surface-negative-soft/20'
    : isLow
      ? 'bg-surface-warning-soft/20'
      : 'bg-surface-primary-soft/20';

  const textClass = isExhausted
    ? 'text-content-negative-soft'
    : isLow
      ? 'text-content-warning-soft'
      : 'text-content-primary-soft';

  const barClass = isExhausted
    ? 'bg-content-negative-soft'
    : isLow
      ? 'bg-content-warning-soft'
      : 'bg-content-primary-soft';

  return (
    <div className={`rounded-lg px-3 py-2 ${bgClass}`}>
      <VStack className="gap-1.5 items-start">
        <HStack className="gap-2 items-center w-full justify-between">
          <HStack className="gap-1.5 items-center">
            <Icon name="sparkles" label="Trial" className={`w-3.5 h-3.5 ${textClass}`} />
            <Text level="caption" className={textClass}>
              {isExhausted ? 'Trial exhausted' : 'Free Trial'}
            </Text>
          </HStack>
          <Text level="caption" className="text-content-layout-3">
            {remaining_tokens_display}/{limit_tokens_display}
          </Text>
        </HStack>
        <div className="w-full h-1 rounded-full bg-surface-raised overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-300 ${barClass}`}
            style={{ width: `${Math.max(percent_remaining, 0)}%` }}
          />
        </div>
      </VStack>
    </div>
  );
}
