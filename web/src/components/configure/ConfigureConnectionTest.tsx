/**
 * Component for testing database connection status
 */

import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { Card } from '@rs/ui-new/card';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Show } from '@rs/ui-new/show';
import { Spinner } from '@rs/ui-new/spinner';
import { m } from '@rs/ui-new/motion';
import type { ConfigureConnectionStatus } from '../../types/configure';

interface ConfigureConnectionTestProps {
  result: ConfigureConnectionStatus | null;
  isLoading?: boolean;
}

export function ConfigureConnectionTest({ result, isLoading }: ConfigureConnectionTestProps) {
  if (isLoading) {
    return (
      <Card className="w-full">
        <Card.Content>
          <HStack className="gap-3 items-center">
            <Spinner size="base" />
            <Text level="body-medium" className="text-content-layout-2">
              Testing connection...
            </Text>
          </HStack>
        </Card.Content>
      </Card>
    );
  }

  if (!result) {
    return null;
  }

  return (
    <m.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2 }}
    >
      <Card className={`w-full ${result.connected ? 'border-border-positive-soft' : 'border-border-negative-soft'}`}>
        <Card.Content>
          <HStack className="gap-4 items-start">
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${
              result.connected
                ? 'bg-surface-positive-soft/20'
                : 'bg-surface-negative-soft/20'
            }`}>
              <Icon
                name={result.connected ? "tick" : "close"}
                label={result.connected ? "Success" : "Failed"}
                className={`w-5 h-5 ${
                  result.connected
                    ? 'text-content-positive-soft'
                    : 'text-content-negative-soft'
                }`}
              />
            </div>
            <VStack className="gap-1 items-start">
              <Text level="label-medium" className={
                result.connected
                  ? 'text-content-positive-soft'
                  : 'text-content-negative-soft'
              }>
                {result.connected ? 'Connection successful' : 'Connection failed'}
              </Text>
              <Show when={!!result.engine}>
                <Text level="body-small" className="text-content-layout-2">
                  Server: {result.engine}
                </Text>
              </Show>
              <Show when={!!result.error}>
                <Text level="body-small" className="text-content-negative-soft">
                  {result.error}
                </Text>
              </Show>
            </VStack>
          </HStack>
        </Card.Content>
      </Card>
    </m.div>
  );
}
