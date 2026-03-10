/**
 * Filter controls for Scan — CLI parity with rdst scan options
 */

import { useState } from 'react';
import { Button } from '@rs/ui-new/button';
import { BaseInputText } from '@rs/ui-new/base-input-text';
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Card } from '@rs/ui-new/card';
import { Show } from '@rs/ui-new/show';
import { m, AnimatePresence } from '@rs/ui-new/motion';
import type { ScanState } from '../../types/scan';
import { DirectoryPicker } from './DirectoryPicker';

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
              <DirectoryPicker
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
                  label="Start Scan"
                  icon="search"
                  iconPosition="left"
                  onClick={onStart}
                  disabled={!hasTarget || !directory.trim()}
                />
              </Show>
            </div>

            {/* Advanced toggle */}
            <button
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
            </button>
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
                  {/* Text inputs row */}
                  <div className="flex flex-wrap gap-4 items-end">
                    <VStack className="gap-1.5 items-start flex-1 min-w-40">
                      <Text as="label" level="label-small" className="text-content-layout-2">
                        Git diff ref
                      </Text>
                      <BaseInputText
                        name="diff"
                        value={diff}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) => setDiff(e.target.value)}
                        placeholder="e.g., HEAD, main, HEAD~1"
                        disabled={isDisabled}
                      />
                    </VStack>

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
                  </div>

                  {/* Toggle row */}
                  <div className="flex flex-wrap gap-6 items-center">
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
                    </Show>

                    <HStack className="gap-2 items-center">
                      <BaseInputSwitch
                        name="dry-run"
                        checked={dryRun}
                        onCheckedChange={(checked) => setDryRun(checked === true)}
                        disabled={isDisabled}
                      />
                      <Text level="label-small" className="text-content-layout-2">
                        Dry run
                      </Text>
                    </HStack>

                    <HStack className="gap-2 items-center">
                      <BaseInputSwitch
                        name="nosave"
                        checked={nosave}
                        onCheckedChange={(checked) => setNosave(checked === true)}
                        disabled={isDisabled}
                      />
                      <Text level="label-small" className="text-content-layout-2">
                        Skip registry save
                      </Text>
                    </HStack>

                    <HStack className="gap-2 items-center">
                      <BaseInputSwitch
                        name="check"
                        checked={check}
                        onCheckedChange={(checked) => setCheck(checked === true)}
                        disabled={isDisabled}
                      />
                      <Text level="label-small" className="text-content-layout-2">
                        CI check mode
                      </Text>
                    </HStack>
                  </div>

                  {/* Threshold controls (shown when check mode is on) */}
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
                </div>
              </m.div>
            )}
          </AnimatePresence>
        </Card.Content>
      </Card>
  );
}
