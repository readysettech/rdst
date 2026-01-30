/**
 * Status display for Top Queries monitoring
 */

import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { Tag } from '@rs/ui-new/tag';
import { HStack } from '@rs/ui-new/stack';
import { Show } from '@rs/ui-new/show';
import { m } from '@rs/ui-new/motion';
import type { TopConnectionInfo, TopSourceFallback, TopState } from '../../types/top';

interface TopStatusProps {
  state: TopState;
  connectionInfo: TopConnectionInfo | null;
  sourceFallback: TopSourceFallback | null;
  runtimeSeconds: number;
  totalTracked: number;
  newlySaved: number;
  isRealtime: boolean;
}

function StatItem({ label, value, icon, highlight = false }: { label: string; value: string | number; icon?: string; highlight?: boolean }) {
  return (
    <HStack className="gap-2 items-center">
      {icon && <Icon name={icon as "observe" | "database" | "layers" | "speedometer" | "folder-file"} label={label} className="w-3.5 h-3.5 text-content-layout-3" />}
      <Text level="caption" className="text-content-layout-3">
        {label}:
      </Text>
      <Text level="label-small" className={highlight ? "text-content-primary-soft font-medium" : "text-content-layout-1"}>
        {value}
      </Text>
    </HStack>
  );
}

export function TopStatus({
  state,
  connectionInfo,
  sourceFallback,
  runtimeSeconds,
  totalTracked,
  newlySaved,
  isRealtime,
}: TopStatusProps) {
  const formatDuration = (seconds: number): string => {
    if (seconds < 60) {
      return `${seconds.toFixed(1)}s`;
    }
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}m ${secs}s`;
  };

  return (
    <m.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="rounded-xl bg-surface-layout-2/50 border border-border-layout-1 px-4 py-3"
    >
      <HStack className="flex-wrap gap-x-6 gap-y-2 items-center">
        <Show when={connectionInfo !== null}>
          <StatItem label="Target" value={connectionInfo?.target || ''} icon="database" />
          <div className="w-px h-4 bg-border-layout-1" />
          <StatItem label="Engine" value={connectionInfo?.engine || ''} />
          <div className="w-px h-4 bg-border-layout-1" />
          <HStack className="gap-2 items-center">
            <Text level="caption" className="text-content-layout-3">
              Source:
            </Text>
            <Tag
              size="small"
              variant="informative"
              modifier="ghost"
              label={connectionInfo?.source || 'unknown'}
            />
          </HStack>
        </Show>

        <Show when={isRealtime && state === 'streaming'}>
          <div className="w-px h-4 bg-border-layout-1" />
          <HStack className="gap-2 items-center">
            <div className="w-2 h-2 rounded-full bg-content-positive-solid animate-pulse" />
            <Text level="label-small" className="text-content-positive-solid font-medium">
              {formatDuration(runtimeSeconds)}
            </Text>
          </HStack>
          <div className="w-px h-4 bg-border-layout-1" />
          <StatItem label="Tracked" value={totalTracked} icon="layers" />
        </Show>

        <Show when={newlySaved > 0}>
          <div className="w-px h-4 bg-border-layout-1" />
          <HStack className="gap-2 items-center">
            <Icon name="tick-double" label="Saved" className="w-3.5 h-3.5 text-content-positive-soft" />
            <Tag
              size="small"
              variant="positive"
              modifier="ghost"
              label={`${newlySaved} saved`}
            />
          </HStack>
        </Show>

        <Show when={sourceFallback !== null}>
          <div className="w-px h-4 bg-border-layout-1" />
          <Tag
            size="small"
            variant="warning"
            modifier="ghost"
            label={`Fallback: ${sourceFallback?.from_source} → ${sourceFallback?.to_source}`}
          />
        </Show>
      </HStack>
    </m.div>
  );
}
