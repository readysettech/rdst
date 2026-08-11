/**
 * Filter controls for Scan — CLI parity with rdst scan options
 */

import { BaseInputSwitch } from '@rs/ui-new/base-input-switch';
import { BaseInputText } from '@rs/ui-new/base-input-text';
import { Button } from '@rs/ui-new/button';
import { Card } from '@rs/ui-new/card';
import { Icon } from '@rs/ui-new/icon';
import { AnimatePresence, m } from '@rs/ui-new/motion';
import { Pressable } from '@rs/ui-new/pressable';
import { Show } from '@rs/ui-new/show';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Text } from '@rs/ui-new/text';
import { useState } from 'react';
import type { ScanState } from '../../types/scan';
import { PathPicker } from '../PathPicker';

interface ScanFiltersProps {
  directory: string;
  setDirectory: (dir: string) => void;
  recentDirs: string[];
  analyze: boolean;
  setAnalyze: (v: boolean) => void;
  shallow: boolean;
  setShallow: (v: boolean) => void;
  dryRun: boolean;
  setDryRun: (v: boolean) => void;
  diff: string;
  setDiff: (v: string) => void;
  filePattern: string;
  setFilePattern: (v: string) => void;
  nosave: boolean;
  setNosave: (v: boolean) => void;
  check: boolean;
  setCheck: (v: boolean) => void;
  warnThreshold: number;
  setWarnThreshold: (v: number) => void;
  failThreshold: number;
  setFailThreshold: (v: number) => void;
  state: ScanState;
  onStart: () => void;
  onCancel: () => void;
  hasTarget: boolean;
  targetName?: string | null;
}

export function ScanFilters({
  directory,
  setDirectory,
  recentDirs,
  analyze,
  setAnalyze,
  shallow,
  setShallow,
  dryRun,
  setDryRun,
  diff,
  setDiff,
  filePattern,
  setFilePattern,
  nosave,
  setNosave,
  check,
  setCheck,
  warnThreshold,
  setWarnThreshold,
  failThreshold,
  setFailThreshold,
  state,
  onStart,
  onCancel,
  hasTarget,
  targetName,
}: ScanFiltersProps) {
  const isScanning = state === 'scanning';
  const isDisabled = isScanning;
  const [showAdvanced, setShowAdvanced] = useState(false);

  return (
      <Card className="w-full overflow-hidden">
        <Card.Content className="p-0">
          {/* Main controls */}
          <div className="p-5 space-y-4">
            {/* Directory path + Start button row */}
            <div className="flex gap-3 items-end">
              <PathPicker
                value={directory}
                onChange={setDirectory}
                disabled={isDisabled}
                recentDirs={recentDirs}
              />
              <Show when={isScanning}>
                <Button
                  variant="negative"
                  modifier="solid"
                  label="Cancel"
                  icon="close"
                  iconPosition="left"
                  onClick={onCancel}
                />
              </Show>
              <Show when={!isScanning}>
                <Button
                  variant="rising"
                  modifier="solid"
                  label="Start scan"
                  icon="search"
                  iconPosition="left"
                  onClick={onStart}
                  disabled={!hasTarget || !directory.trim()}
                />
              </Show>
            </div>

            {/* Secondary — the DB context the analysis runs against */}
            <Show when={!!targetName}>
              <HStack className="gap-1.5 items-center">
                <Text level="label-small" className="text-content-layout-3">
                  Analyze against
                </Text>
                <Text level="mono-small" className="text-content-layout-2">
                  {targetName}
                </Text>
              </HStack>
            </Show>

            {/* Tertiary — advanced disclosure + experimental caveat */}
            <VStack className="gap-2.5 items-start">
              {/* Advanced disclosure trigger — kept as a hand-roll: a quiet
                  muted-text toggle whose panel renders as a separate full-width
                  region below, so a filled ui-new Button and the single-container
                  Disclosure would both change its look. */}
              <Pressable
                type="button"
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="flex items-center gap-1.5 text-content-layout-3 hover:text-content-layout-2 transition-colors cursor-pointer"
              >
                <Icon
                  name={showAdvanced ? 'chevron-down' : 'chevron-right'}
                  label="Toggle"
                  className="w-3 h-3"
                />
                <Text level="caption" className="inherit">
                  Advanced options
                </Text>
              </Pressable>

              {/* Honesty caveat — the converted SQL is AI-generated */}
              <HStack className="gap-1.5 items-center">
                <Icon
                  name="alert"
                  label="Experimental"
                  className="w-3.5 h-3.5 text-content-warning-soft shrink-0"
                />
                <Text level="caption" className="text-content-warning-soft">
                  Experimental · AI-converted SQL — verify before use
                </Text>
              </HStack>
            </VStack>
          </div>

          {/* Advanced settings */}
          <AnimatePresence>
            {showAdvanced && (
              <m.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="px-5 pb-5 space-y-5 border-t border-border-layout-1 pt-4">
                  {/* ── Scope ── which files and revisions to look at */}
                  <VStack className="gap-3 items-stretch">
                    <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                      Scope
                    </Text>
                    <div className="flex flex-wrap gap-4 items-end">
                      <VStack className="gap-1.5 items-start flex-1 min-w-40">
                        <Text as="label" level="label-small" className="text-content-layout-2">
                          File pattern
                        </Text>
                        <BaseInputText
                          name="file-pattern"
                          value={filePattern}
                          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setFilePattern(e.target.value)}
                          placeholder="e.g., *.py, services/*.ts"
                          disabled={isDisabled}
                        />
                      </VStack>

                      <VStack className="gap-1.5 items-start flex-1 min-w-40">
                        <Text as="label" level="label-small" className="text-content-layout-2">
                          Only changed vs. git ref
                        </Text>
                        <BaseInputText
                          name="diff"
                          value={diff}
                          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDiff(e.target.value)}
                          placeholder="e.g., HEAD, main, HEAD~1"
                          disabled={isDisabled}
                        />
                      </VStack>
                    </div>
                  </VStack>

                  {/* ── After scanning ── what to do with the extracted queries */}
                  <VStack className="gap-3 items-stretch border-t border-border-layout-1 pt-4">
                    <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                      After scanning
                    </Text>

                    {/* Analyze performance + cost disclosure */}
                    <VStack className="gap-1 items-start">
                      <HStack className="gap-2 items-center">
                        <BaseInputSwitch
                          name="analyze"
                          checked={analyze}
                          onCheckedChange={(checked) => setAnalyze(checked === true)}
                          disabled={isDisabled}
                        />
                        <Text level="label-small" className="text-content-layout-2">
                          Analyze performance
                        </Text>
                      </HStack>
                      <Show when={analyze}>
                        <Text level="caption" className="text-content-layout-3 pl-11">
                          Runs EXPLAIN and spins a temporary Readyset container.
                        </Text>
                      </Show>
                    </VStack>

                    {/* Shallow — analysis depth (schema-only vs deep EXPLAIN) */}
                    <HStack className="gap-2 items-center">
                      <BaseInputSwitch
                        name="shallow"
                        checked={shallow}
                        onCheckedChange={(checked) => setShallow(checked === true)}
                        disabled={isDisabled}
                      />
                      <Text level="label-small" className="text-content-layout-2">
                        Shallow (schema-only)
                      </Text>
                    </HStack>

                    {/* Save findings — positive reframe of the `nosave` flag.
                        Display only: checked = save (nosave === false); the
                        underlying value/behavior is unchanged. */}
                    <HStack className="gap-2 items-center">
                      <BaseInputSwitch
                        name="nosave"
                        checked={!nosave}
                        onCheckedChange={(checked) => setNosave(checked !== true)}
                        disabled={isDisabled}
                      />
                      <Text level="label-small" className="text-content-layout-2">
                        Save findings to Queries
                      </Text>
                    </HStack>

                    {/* Dry run */}
                    <HStack className="gap-2 items-center">
                      <BaseInputSwitch
                        name="dry-run"
                        checked={dryRun}
                        onCheckedChange={(checked) => setDryRun(checked === true)}
                        disabled={isDisabled}
                      />
                      <Text level="label-small" className="text-content-layout-2">
                        Dry run (preview, change nothing)
                      </Text>
                    </HStack>
                  </VStack>

                  {/* ── CI gate ── fail the build on a low score */}
                  <VStack className="gap-3 items-stretch border-t border-border-layout-1 pt-4">
                    <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                      CI gate
                    </Text>

                    <HStack className="gap-2 items-center">
                      <BaseInputSwitch
                        name="check"
                        checked={check}
                        onCheckedChange={(checked) => setCheck(checked === true)}
                        disabled={isDisabled}
                      />
                      <Text level="label-small" className="text-content-layout-2">
                        Fail build below score
                      </Text>
                    </HStack>

                    {/* Thresholds — only when the gate is on */}
                    <Show when={check}>
                      <div className="flex gap-4 items-end">
                        <VStack className="gap-1.5 items-start w-32">
                          <Text as="label" level="label-small" className="text-content-layout-2">
                            Warn threshold
                          </Text>
                          <BaseInputText
                            name="warn-threshold"
                            value={String(warnThreshold)}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                              setWarnThreshold(Number(e.target.value) || 60)
                            }
                            disabled={isDisabled}
                          />
                        </VStack>
                        <VStack className="gap-1.5 items-start w-32">
                          <Text as="label" level="label-small" className="text-content-layout-2">
                            Fail threshold
                          </Text>
                          <BaseInputText
                            name="fail-threshold"
                            value={String(failThreshold)}
                            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                              setFailThreshold(Number(e.target.value) || 40)
                            }
                            disabled={isDisabled}
                          />
                        </VStack>
                      </div>
                    </Show>
                  </VStack>
                </div>
              </m.div>
            )}
          </AnimatePresence>
        </Card.Content>
      </Card>
  );
}
