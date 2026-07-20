/**
 * Header for Top Queries page.
 *
 * Region A of the redesign: identity + status + context. The old free-standing
 * `TopStatus` bar is folded in here as a quiet inline "Target · source" strip so
 * "where am I / what am I looking at" sits next to the page title, not below the
 * cockpit. The live timer / tracked count only appears while streaming.
 * [USE-002, USE-003, VIS-113 — merges TopStatus]
 */

import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { Tag } from '@rs/ui-new/tag';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Show } from '@rs/ui-new/show';
import { m } from '@rs/ui-new/motion';
import type { TopSourceFallback, TopState } from '../../types/top';

interface TopHeaderProps {
  state: TopState;
  /** Resolved (or, before a run, selected) target name. */
  targetLabel: string;
  /** Resolved (or, before a run, requested) query source. */
  sourceLabel: string;
  /** Resolved DB engine, once a run has reported it (undefined pre-run). */
  engineLabel?: string;
  runtimeSeconds: number;
  totalTracked: number;
  newlySaved: number;
  isRealtime: boolean;
  sourceFallback: TopSourceFallback | null;
}

function getStatusVariant(state: TopState): 'positive' | 'warning' | 'negative' | 'informative' {
  switch (state) {
    case 'streaming':
      return 'positive';
    case 'loading':
      return 'warning';
    case 'error':
      return 'negative';
    case 'complete':
      return 'informative';
    default:
      return 'informative';
  }
}

function getStatusLabel(state: TopState): string {
  switch (state) {
    case 'idle':
      return 'Ready';
    case 'loading':
      return 'Loading';
    case 'streaming':
      return 'Live';
    case 'complete':
      return 'Complete';
    case 'error':
      return 'Error';
    default:
      return state;
  }
}

function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds.toFixed(1)}s`;
  }
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}m ${secs}s`;
}

export function TopHeader({
  state,
  targetLabel,
  sourceLabel,
  engineLabel,
  runtimeSeconds,
  totalTracked,
  newlySaved,
  isRealtime,
  sourceFallback,
}: TopHeaderProps) {
  const isStreaming = isRealtime && state === 'streaming';

  return (
    <m.div
      className="space-y-4"
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <HStack className="justify-between items-start gap-4 flex-wrap">
        <HStack className="gap-4 items-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-warning-soft flex items-center justify-center">
            <Icon name="observe" label="Slow Queries" className="w-6 h-6 text-content-primary-soft" />
          </div>
          <VStack className="gap-1 items-start">
            <HStack className="gap-3 items-center">
              <Text as="h1" level="headline-3" className="text-content-layout-1">
                Slow Queries
              </Text>
              <Tag
                size="small"
                variant={getStatusVariant(state)}
                modifier={state === 'streaming' ? 'solid' : 'ghost'}
                label={getStatusLabel(state)}
              />
            </HStack>
            <Text level="body-small" className="text-content-layout-3">
              Your slowest queries, worst first.
            </Text>
          </VStack>
        </HStack>

        {/* Quiet "where am I" context strip — folds in the old TopStatus bar. */}
        <HStack className="gap-x-4 gap-y-1 items-center flex-wrap justify-end pt-1">
          <HStack className="gap-1.5 items-center">
            <Text level="caption" className="text-content-layout-3">
              Target:
            </Text>
            <Text level="label-small" className="text-content-layout-2">
              {targetLabel}
            </Text>
          </HStack>
          <Text level="caption" className="text-content-layout-3">
            ·
          </Text>
          <HStack className="gap-1.5 items-center">
            <Text level="caption" className="text-content-layout-3">
              source:
            </Text>
            <Text level="label-small" className="text-content-layout-2">
              {sourceLabel}
            </Text>
          </HStack>

          <Show when={!!engineLabel}>
            <Text level="caption" className="text-content-layout-3">
              ·
            </Text>
            <HStack className="gap-1.5 items-center">
              <Text level="caption" className="text-content-layout-3">
                engine:
              </Text>
              <Text level="label-small" className="text-content-layout-2">
                {engineLabel}
              </Text>
            </HStack>
          </Show>

          <Show when={isStreaming}>
            <HStack className="gap-1.5 items-center">
              <div className="w-2 h-2 rounded-full bg-content-positive-solid animate-pulse" />
              <Text level="label-small" className="text-content-positive-solid font-medium">
                {formatDuration(runtimeSeconds)}
              </Text>
            </HStack>
            <HStack className="gap-1.5 items-center">
              <Text level="caption" className="text-content-layout-3">
                Tracked:
              </Text>
              <Text level="label-small" className="text-content-layout-2">
                {totalTracked}
              </Text>
            </HStack>
          </Show>

          <Show when={newlySaved > 0}>
            <Tag size="small" variant="positive" modifier="ghost" label={`${newlySaved} saved`} />
          </Show>

          <Show when={sourceFallback !== null}>
            <Tag
              size="small"
              variant="warning"
              modifier="ghost"
              label={`Fallback: ${sourceFallback?.from_source} → ${sourceFallback?.to_source}`}
            />
          </Show>
        </HStack>
      </HStack>
    </m.div>
  );
}
