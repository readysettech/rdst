/**
 * Header for Top Queries page
 */

import { Button } from '@rs/ui-new/button';
import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { Tag } from '@rs/ui-new/tag';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Show } from '@rs/ui-new/show';
import { m } from '@rs/ui-new/motion';
import type { TopState } from '../../types/top';

interface TopHeaderProps {
  state: TopState;
  queriesCount: number;
  onSaveAll: () => void;
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

export function TopHeader({ state, queriesCount, onSaveAll }: TopHeaderProps) {
  const canSave = queriesCount > 0 && (state === 'complete' || state === 'streaming');

  return (
    <m.div
      className="space-y-4"
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <HStack className="justify-between items-start">
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
              Monitor and analyze slow queries from your database
            </Text>
          </VStack>
        </HStack>

        <Show when={canSave}>
          <m.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.2 }}
          >
            <Button
              variant="primary"
              modifier="outline"
              label="Save All to Registry"
              icon="add"
              iconPosition="left"
              onClick={onSaveAll}
            />
          </m.div>
        </Show>
      </HStack>
    </m.div>
  );
}
