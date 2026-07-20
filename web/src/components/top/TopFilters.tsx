/**
 * Scope toolbar for Top Queries (region B of the redesign).
 *
 * The old "Query Filters" cockpit card — up to eight controls visible at once —
 * is dissolved into a slim, borderless toolbar: a Historical/Realtime segmented
 * control, a collapsed-by-default `Filters ▾` disclosure holding the advanced
 * knobs, and the single primary action. Sort lives in the results header.
 * [VIS-104, VIS-114, USE-008; brief: remove/defer]
 *
 * Auto-save is a *start-time* request param, so its control must be reachable
 * BEFORE the first run — the results-header switch only exists once queries are
 * listed, which is too late (the first run silently fires auto_save=true). So the
 * Save-automatically switch also renders inside this pre-run Filters panel,
 * sharing the same state as the results-header one. `sort/setSort` stay optional
 * for back-compat with callers/tests but are no longer rendered here. [C-09]
 */

import { Button } from '@rs/ui-new/button';
import { BaseInputText } from '@rs/ui-new/base-input-text';
import { BaseInputSelect } from '@rs/ui-new/base-input-select';
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch';
import { Label } from '@rs/ui-new/label';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Show } from '@rs/ui-new/show';
import { m, AnimatePresence } from '@rs/ui-new/motion';
import { useDisclosure } from '@rs/ui-new/use-disclosure';
import type { ReactNode } from 'react';
import type { TopMode, TopState } from '../../types/top';

interface TopFiltersProps {
  mode: TopMode;
  setMode: (mode: TopMode) => void;
  source: string;
  setSource: (source: string) => void;
  /** Retained for back-compat; Sort now lives in the results header. */
  sort?: string;
  setSort?: (sort: string) => void;
  limit: number;
  setLimit: (limit: number) => void;
  filterPattern: string;
  setFilterPattern: (pattern: string) => void;
  filterPatternError?: string | null;
  minFreq: number;
  setMinFreq: (value: number) => void;
  minLoadPct: number;
  setMinLoadPct: (value: number) => void;
  duration: number;
  setDuration: (duration: number) => void;
  /** Save-automatically preference — rendered here (pre-run reachable) and, once
   * results exist, mirrored in the results header off the same state. [C-09] */
  autoSave?: boolean;
  setAutoSave?: (autoSave: boolean) => void;
  state: TopState;
  onStart: () => void;
  onStop: () => void;
  hasTarget: boolean;
  /** Page hides the toolbar's primary action at idle so the empty-state CTA is the only one. */
  primaryActionHidden?: boolean;
}

const sourceOptions = [
  { value: 'auto', label: 'Auto-detect' },
  { value: 'pg_stat', label: 'pg_stat_statements' },
  { value: 'activity', label: 'pg_stat_activity' },
  { value: 'digest', label: 'MySQL digest' },
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

/** A labelled filter field — label is programmatically associated (a11y L4). */
function FilterField({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <VStack className="gap-1.5 items-start w-full">
      <Label htmlFor={id} className="text-label-small text-content-layout-2">
        {label}
      </Label>
      {children}
    </VStack>
  );
}

export function TopFilters({
  mode,
  setMode,
  source,
  setSource,
  limit,
  setLimit,
  filterPattern,
  setFilterPattern,
  filterPatternError,
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
  primaryActionHidden = false,
}: TopFiltersProps) {
  const isStreaming = state === 'streaming';
  const isLoading = state === 'loading';
  const isDisabled = isStreaming || isLoading;

  const [filtersOpen, setFiltersOpen] = useDisclosure({});

  // Count active (non-default) advanced filters so the disclosure hints at what's tuned.
  const activeCount =
    (source !== 'auto' ? 1 : 0) +
    (minFreq > 0 ? 1 : 0) +
    (minLoadPct > 0 ? 1 : 0) +
    (filterPattern.trim() ? 1 : 0) +
    (mode === 'realtime' && duration > 0 ? 1 : 0);

  // Primary action label matches the page's job; the "Get Top Queries" branch is
  // only reachable at idle, which the page hides in favour of the empty-state CTA.
  const primaryLabel =
    mode === 'realtime'
      ? 'Start live monitoring'
      : state === 'idle'
        ? 'Get Top Queries'
        : 'Find slow queries';

  return (
    <m.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: 0.05 }}
      className="space-y-3"
    >
      {/* Toolbar row */}
      <HStack className="justify-between items-center gap-3 flex-wrap">
        <HStack className="gap-3 items-center flex-wrap">
          {/* Historical / Realtime segmented control */}
          <div className="flex gap-1 p-1 bg-surface-layout-2 rounded-xl">
            {modeOptions.map((option) => {
              const selected = mode === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => !isDisabled && setMode(option.value as TopMode)}
                  disabled={isDisabled}
                  aria-pressed={selected}
                  className={`px-3 h-8 rounded-lg text-button-small transition-colors ${
                    selected
                      ? 'bg-surface-layout-1 text-content-layout-1 shadow-small'
                      : 'text-content-layout-3 hover:text-content-layout-2'
                  } ${isDisabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                >
                  <HStack className="gap-1.5 items-center">
                    <Icon
                      name={option.value === 'realtime' ? 'play' : 'observe'}
                      label={option.label}
                      className="w-3.5 h-3.5"
                    />
                    <span>{option.label}</span>
                  </HStack>
                </button>
              );
            })}
          </div>

          {/* Filters ▾ disclosure trigger */}
          <button
            type="button"
            onClick={() => setFiltersOpen(!filtersOpen)}
            disabled={isDisabled}
            aria-expanded={filtersOpen}
            aria-controls="top-filters-panel"
            className={`flex items-center gap-1.5 px-3 h-8 rounded-lg text-button-small text-content-layout-2 hover:text-content-layout-1 hover:bg-surface-layout-2 transition-colors ${
              isDisabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
            }`}
          >
            <Icon name="filter" label="Filters" className="w-3.5 h-3.5" />
            <span>Filters</span>
            <Show when={activeCount > 0}>
              <span className="min-w-4 h-4 px-1 rounded-full bg-surface-primary-soft text-content-primary-soft text-label-extra-small flex items-center justify-center">
                {activeCount}
              </span>
            </Show>
            <Icon
              name="chevron-down"
              label="Toggle filters"
              className={`w-3.5 h-3.5 transition-transform ${filtersOpen ? 'rotate-180' : ''}`}
            />
          </button>
        </HStack>

        {/* Primary action */}
        <HStack className="gap-2 items-center">
          <Show when={isStreaming}>
            <Button
              variant="negative"
              modifier="solid"
              label="Stop live monitoring"
              icon="close"
              iconPosition="left"
              onClick={onStop}
            />
          </Show>
          <Show when={!isStreaming && !primaryActionHidden}>
            <Button
              variant="rising"
              modifier="solid"
              label={primaryLabel}
              icon={mode === 'realtime' ? 'play' : 'search'}
              iconPosition="left"
              onClick={onStart}
              loading={isLoading}
              disabled={!hasTarget || !!filterPatternError}
            />
          </Show>
        </HStack>
      </HStack>

      {/* Collapsible advanced-filter panel */}
      <AnimatePresence initial={false}>
        {filtersOpen && (
          <m.div
            id="top-filters-panel"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="rounded-xl bg-surface-layout-2/40 p-4 shadow-elevation-1">
              <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                <Show when={mode === 'historical'}>
                  <FilterField id="top-source" label="Source">
                    <BaseInputSelect
                      id="top-source"
                      name="source"
                      options={sourceOptions}
                      value={source}
                      onValueChange={setSource}
                      disabled={isDisabled}
                    />
                  </FilterField>

                  <FilterField id="top-min-freq" label="Min Frequency">
                    <BaseInputText
                      id="top-min-freq"
                      name="min-freq"
                      type="number"
                      value={String(minFreq)}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                        setMinFreq(Math.max(0, Number(e.target.value) || 0))
                      }
                      placeholder="0"
                      disabled={isDisabled}
                    />
                  </FilterField>

                  <FilterField id="top-min-load" label="Min Load %">
                    <BaseInputText
                      id="top-min-load"
                      name="min-load"
                      type="number"
                      value={String(minLoadPct)}
                      onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                        setMinLoadPct(Math.max(0, Number(e.target.value) || 0))
                      }
                      placeholder="0"
                      disabled={isDisabled}
                    />
                  </FilterField>
                </Show>

                <FilterField id="top-limit" label="Limit">
                  <BaseInputSelect
                    id="top-limit"
                    name="limit"
                    options={limitOptions}
                    value={String(limit)}
                    onValueChange={(value) => setLimit(Number(value) || 10)}
                    disabled={isDisabled}
                  />
                </FilterField>

                <Show when={mode === 'realtime'}>
                  <FilterField id="top-duration" label="Duration">
                    <BaseInputSelect
                      id="top-duration"
                      name="duration"
                      options={durationOptions}
                      value={String(duration)}
                      onValueChange={(value) => setDuration(Number(value) || 0)}
                      disabled={isDisabled}
                    />
                  </FilterField>
                </Show>

                <Show when={mode === 'historical'}>
                  <div className="col-span-2 md:col-span-3">
                    <FilterField id="top-filter" label="Filter (regex)">
                      <BaseInputText
                        id="top-filter"
                        name="filter"
                        value={filterPattern}
                        error={!!filterPatternError}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setFilterPattern(e.target.value)
                        }
                        placeholder="e.g., SELECT.*users"
                        disabled={isDisabled}
                        aria-invalid={!!filterPatternError}
                        aria-describedby={filterPatternError ? 'top-filter-error' : undefined}
                      />
                      <Show when={!!filterPatternError}>
                        <div id="top-filter-error">
                          <Text level="caption" className="text-content-negative-plain">
                            Invalid regex — {filterPatternError}
                          </Text>
                        </div>
                      </Show>
                    </FilterField>
                  </div>
                </Show>
              </div>

              {/* Auto-save preference — reachable BEFORE the first run so the
                  auto_save start param is a deliberate choice, not a silent
                  default. Shares state with the results-header switch. [C-09] */}
              <Show when={!!setAutoSave}>
                <div className="mt-4 pt-4 border-t border-border-layout-1">
                  <HStack className="justify-between items-center gap-3">
                    <VStack className="gap-0.5 items-start">
                      <Label
                        htmlFor="top-auto-save-filter"
                        className="text-label-small text-content-layout-2"
                      >
                        Save automatically
                      </Label>
                      <Text level="caption" className="text-content-layout-3">
                        Add newly found queries to the registry on each run.
                      </Text>
                    </VStack>
                    <BaseInputSwitch
                      id="top-auto-save-filter"
                      name="auto-save-filter"
                      checked={!!autoSave}
                      onCheckedChange={(checked) => setAutoSave?.(checked === true)}
                      disabled={isDisabled}
                    />
                  </HStack>
                </div>
              </Show>
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </m.div>
  );
}
