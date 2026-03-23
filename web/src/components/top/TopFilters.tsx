/**
 * Filter controls for Top Queries
 */

import { Button } from '@rs/ui-new/button';
import { BaseInputText } from '@rs/ui-new/base-input-text';
import { BaseInputSelect } from '@rs/ui-new/base-input-select';
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Card } from '@rs/ui-new/card';
import { Show } from '@rs/ui-new/show';
import { m } from '@rs/ui-new/motion';
import type { TopMode, TopState } from '../../types/top';

interface TopFiltersProps {
  mode: TopMode;
  setMode: (mode: TopMode) => void;
  source: string;
  setSource: (source: string) => void;
  sort: string;
  setSort: (sort: string) => void;
  limit: number;
  setLimit: (limit: number) => void;
  filterPattern: string;
  setFilterPattern: (pattern: string) => void;
  minFreq: number;
  setMinFreq: (value: number) => void;
  minLoadPct: number;
  setMinLoadPct: (value: number) => void;
  duration: number;
  setDuration: (duration: number) => void;
  autoSave: boolean;
  setAutoSave: (autoSave: boolean) => void;
  state: TopState;
  onStart: () => void;
  onStop: () => void;
  hasTarget: boolean;
}

const sourceOptions = [
  { value: 'auto', label: 'Auto-detect' },
  { value: 'pg_stat', label: 'pg_stat_statements' },
  { value: 'activity', label: 'pg_stat_activity' },
  { value: 'digest', label: 'MySQL digest' },
];

const sortOptions = [
  { value: 'total_time', label: 'Total Time' },
  { value: 'freq', label: 'Frequency' },
  { value: 'avg_time', label: 'Avg Time' },
  { value: 'load', label: 'Load %' },
];

const modeOptions = [
  { value: 'historical', label: 'Historical' },
  { value: 'realtime', label: 'Realtime' },
];

const limitOptions = [
  { value: '5', label: '5' },
  { value: '10', label: '10' },
  { value: '20', label: '20' },
  { value: '50', label: '50' },
  { value: '100', label: '100' },
];

const durationOptions = [
  { value: '0', label: 'Unlimited' },
  { value: '10', label: '10s' },
  { value: '30', label: '30s' },
  { value: '60', label: '1m' },
  { value: '300', label: '5m' },
];

export function TopFilters({
  mode,
  setMode,
  source,
  setSource,
  sort,
  setSort,
  limit,
  setLimit,
  filterPattern,
  setFilterPattern,
  minFreq,
  setMinFreq,
  minLoadPct,
  setMinLoadPct,
  duration,
  setDuration,
  autoSave,
  setAutoSave,
  state,
  onStart,
  onStop,
  hasTarget,
}: TopFiltersProps) {
  const isStreaming = state === 'streaming';
  const isLoading = state === 'loading';
  const isDisabled = isStreaming || isLoading;

  return (
    <m.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.1 }}
    >
      <Card className="w-full overflow-hidden">
        <Card.Content className="p-0">
          {/* Header */}
          <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
            <HStack className="justify-between items-center">
              <HStack className="gap-2 items-center">
                <Icon name="settings" label="Filters" className="w-4 h-4 text-content-layout-3" />
                <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                  Query Filters
                </Text>
              </HStack>
              <HStack className="gap-3 items-center">
                <HStack className="gap-2 items-center">
                  <BaseInputSwitch
                    name="auto-save"
                    checked={autoSave}
                    onCheckedChange={(checked) => setAutoSave(checked === true)}
                    disabled={isDisabled}
                  />
                  <Text level="label-small" className="text-content-layout-2">
                    Auto-save to registry
                  </Text>
                </HStack>
              </HStack>
            </HStack>
          </div>

          {/* Filter controls */}
          <div className="p-5 space-y-5">
            {/* Mode selector tabs */}
            <div className="flex gap-2 p-1 bg-surface-layout-2 rounded-lg w-fit">
              {modeOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => !isDisabled && setMode(option.value as TopMode)}
                  disabled={isDisabled}
                  className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${
                    mode === option.value
                      ? 'bg-surface-layout-1 text-content-layout-1 shadow-sm'
                      : 'text-content-layout-3 hover:text-content-layout-2'
                  } ${isDisabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                >
                  <HStack className="gap-2 items-center">
                    <Icon
                      name={option.value === 'realtime' ? 'play' : 'observe'}
                      label={option.label}
                      className="w-4 h-4"
                    />
                    <span>{option.label}</span>
                  </HStack>
                </button>
              ))}
            </div>

            {/* Filter row */}
            <div className="flex flex-wrap gap-4 items-end">
              <Show when={mode === 'historical'}>
                <VStack className="gap-1.5 items-start w-44">
                  <Text as="label" level="label-small" className="text-content-layout-2">
                    Source
                  </Text>
                  <BaseInputSelect
                    name="source"
                    options={sourceOptions}
                    value={source}
                    onValueChange={setSource}
                    disabled={isDisabled}
                  />
                </VStack>

                <VStack className="gap-1.5 items-start w-36">
                  <Text as="label" level="label-small" className="text-content-layout-2">
                    Sort by
                  </Text>
                  <BaseInputSelect
                    name="sort"
                    options={sortOptions}
                    value={sort}
                    onValueChange={setSort}
                    disabled={isDisabled}
                  />
                </VStack>

                <VStack className="gap-1.5 items-start w-32">
                  <Text as="label" level="label-small" className="text-content-layout-2">
                    Min Frequency
                  </Text>
                  <BaseInputText
                    name="min-freq"
                    type="number"
                    value={String(minFreq)}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                      setMinFreq(Math.max(0, Number(e.target.value) || 0))
                    }
                    placeholder="0"
                    disabled={isDisabled}
                  />
                </VStack>

                <VStack className="gap-1.5 items-start w-32">
                  <Text as="label" level="label-small" className="text-content-layout-2">
                    Min Load %
                  </Text>
                  <BaseInputText
                    name="min-load"
                    type="number"
                    value={String(minLoadPct)}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                      setMinLoadPct(Math.max(0, Number(e.target.value) || 0))
                    }
                    placeholder="0"
                    disabled={isDisabled}
                  />
                </VStack>
              </Show>

              <VStack className="gap-1.5 items-start w-24">
                <Text as="label" level="label-small" className="text-content-layout-2">
                  Limit
                </Text>
                <BaseInputSelect
                  name="limit"
                  options={limitOptions}
                  value={String(limit)}
                  onValueChange={(value) => setLimit(Number(value) || 10)}
                  disabled={isDisabled}
                />
              </VStack>

              <Show when={mode === 'realtime'}>
                <VStack className="gap-1.5 items-start w-28">
                  <Text as="label" level="label-small" className="text-content-layout-2">
                    Duration
                  </Text>
                  <BaseInputSelect
                    name="duration"
                    options={durationOptions}
                    value={String(duration)}
                    onValueChange={(value) => setDuration(Number(value) || 0)}
                    disabled={isDisabled}
                  />
                </VStack>
              </Show>

              <VStack className="gap-1.5 items-start flex-1 min-w-48">
                <Text as="label" level="label-small" className="text-content-layout-2">
                  Filter (regex)
                </Text>
                <BaseInputText
                  name="filter"
                  value={filterPattern}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFilterPattern(e.target.value)}
                  placeholder="e.g., SELECT.*users"
                  disabled={isDisabled}
                />
              </VStack>
            </div>
          </div>

          {/* Footer with action */}
          <div className="px-5 py-4 border-t border-border-layout-1 bg-surface-layout-1">
            <HStack className="justify-end">
              <Show when={isStreaming}>
                <Button
                  variant="negative"
                  modifier="solid"
                  label="Stop Monitoring"
                  icon="close"
                  iconPosition="left"
                  onClick={onStop}
                />
              </Show>
              <Show when={!isStreaming}>
                <Button
                  variant="rising"
                  modifier="solid"
                  label={mode === 'realtime' ? 'Start Monitoring' : 'Get Top Queries'}
                  icon="play"
                  iconPosition="left"
                  onClick={onStart}
                  loading={isLoading}
                  disabled={!hasTarget}
                />
              </Show>
            </HStack>
          </div>
        </Card.Content>
      </Card>
    </m.div>
  );
}
