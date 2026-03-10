/**
 * Phase-based progress display for Scan
 */

import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { HStack } from '@rs/ui-new/stack';
import { Card } from '@rs/ui-new/card';
import { m } from '@rs/ui-new/motion';
import type { ScanPhase } from '../../types/scan';

interface ScanProgressProps {
  phase: ScanPhase | null;
  phaseProgress: { current: number; total: number; message: string } | null;
  statusMessage: string | null;
}

const PHASES: { key: ScanPhase; label: string }[] = [
  { key: 'discovery', label: 'Discovery' },
  { key: 'extraction', label: 'Extraction' },
  { key: 'conversion', label: 'Conversion' },
  { key: 'registry', label: 'Registry' },
  { key: 'analysis', label: 'Analysis' },
];

const PHASE_ORDER: Record<ScanPhase, number> = {
  config: 0,
  discovery: 1,
  extraction: 2,
  conversion: 3,
  registry: 4,
  analysis: 5,
};

function getPhaseStatus(
  phaseKey: ScanPhase,
  currentPhase: ScanPhase | null
): 'pending' | 'active' | 'complete' {
  if (!currentPhase) return 'pending';
  const currentOrder = PHASE_ORDER[currentPhase];
  const thisOrder = PHASE_ORDER[phaseKey];
  if (thisOrder < currentOrder) return 'complete';
  if (thisOrder === currentOrder) return 'active';
  return 'pending';
}

export function ScanProgress({
  phase,
  phaseProgress,
  statusMessage,
}: ScanProgressProps) {
  return (
    <m.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <Card className="w-full">
        <Card.Content className="py-4 px-5">
          {/* Phase pipeline */}
          <div className="flex items-center gap-1 mb-3">
            {PHASES.map((p, idx) => {
              const status = getPhaseStatus(p.key, phase);

              return (
                <div key={p.key} className="flex items-center gap-1">
                  {idx > 0 && (
                    <div
                      className={`w-6 h-px ${
                        status === 'pending'
                          ? 'bg-border-layout-1'
                          : 'bg-content-primary-soft'
                      }`}
                    />
                  )}
                  <HStack className="gap-1.5 items-center">
                    {status === 'complete' && (
                      <Icon
                        name="tick"
                        label="Complete"
                        className="w-3.5 h-3.5 text-content-positive-soft"
                      />
                    )}
                    {status === 'active' && (
                      <div className="w-3.5 h-3.5 rounded-full border-2 border-content-primary-soft border-t-transparent animate-spin" />
                    )}
                    {status === 'pending' && (
                      <div className="w-3.5 h-3.5 rounded-full border border-border-layout-1" />
                    )}
                    <Text
                      level="caption"
                      className={
                        status === 'active'
                          ? 'text-content-primary-soft font-medium'
                          : status === 'complete'
                            ? 'text-content-positive-soft'
                            : 'text-content-layout-3'
                      }
                    >
                      {p.label}
                    </Text>
                  </HStack>
                </div>
              );
            })}
          </div>

          {/* Current status message */}
          <HStack className="gap-2 items-center">
            {phaseProgress ? (
              <>
                <Text level="mono-small" className="text-content-layout-2">
                  {phaseProgress.message}
                </Text>
                <Text level="mono-small" className="text-content-layout-3">
                  ({phaseProgress.current}/{phaseProgress.total})
                </Text>
              </>
            ) : (
              <Text level="mono-small" className="text-content-layout-2">
                {statusMessage || 'Initializing...'}
              </Text>
            )}
          </HStack>
        </Card.Content>
      </Card>
    </m.div>
  );
}
