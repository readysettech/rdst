/**
 * Inline connection-test result — rendered beneath the row whose Test button was
 * clicked (not floating at the top of the page). Names the target, condenses the
 * raw `SELECT version()` dump to product + version, and is dismissable. The
 * loading branch is render-gated by the parent per-row so the spinner is actually
 * reachable while a test is in flight. [USE-099, USE-025, USE-008]
 */

import { Text } from '@rs/ui-new/text';
import { Icon } from '@rs/ui-new/icon';
import { HStack, VStack } from '@rs/ui-new/stack';
import { Show } from '@rs/ui-new/show';
import { Spinner } from '@rs/ui-new/spinner';
import { m } from '@rs/ui-new/motion';
import type { ConfigureConnectionStatus } from '../../types/configure';

interface ConfigureConnectionTestProps {
  result: ConfigureConnectionStatus | null;
  isLoading?: boolean;
  /** Name of the target being tested — shown while loading (no result yet). */
  targetName?: string;
  onDismiss?: () => void;
}

/**
 * Condense the raw server banner to product + version.
 * "PostgreSQL 17.10 (Debian 17.10-1) on aarch64-…, compiled by gcc …"
 *   → "PostgreSQL 17.10"
 */
function condenseServerVersion(raw?: string): string | null {
  if (!raw) return null;
  const head = raw.split(/[(,]/)[0]?.trim() ?? '';
  if (!head) return null;
  return head.length > 60 ? `${head.slice(0, 57)}…` : head;
}

export function ConfigureConnectionTest({
  result,
  isLoading,
  targetName,
  onDismiss,
}: ConfigureConnectionTestProps) {
  if (isLoading) {
    return (
      <div
        aria-live="polite"
        className="w-full rounded-xl border border-border-layout-1 bg-surface-layout-2/50 px-3 py-2.5"
      >
        <HStack className="gap-2.5 items-center">
          <Spinner size="base" color="layout" />
          <Text level="body-small" className="text-content-layout-2">
            Testing {targetName ? `“${targetName}”` : 'connection'}…
          </Text>
        </HStack>
      </div>
    );
  }

  if (!result) {
    return null;
  }

  const version = condenseServerVersion(result.engine);

  return (
    <m.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className={`w-full rounded-xl border px-3 py-2.5 ${
        result.connected
          ? 'border-border-positive-soft bg-surface-positive-soft/10'
          : 'border-border-negative-soft bg-surface-negative-soft/10'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <HStack className="gap-2 items-start min-w-0">
          <Icon
            name={result.connected ? 'tick' : 'close'}
            label={result.connected ? 'Connected' : 'Failed'}
            className={`w-4 h-4 mt-0.5 shrink-0 ${
              result.connected ? 'text-content-positive-soft' : 'text-content-negative-soft'
            }`}
          />
          <VStack className="gap-0.5 items-start min-w-0">
            <Text
              level="label-small"
              className={`truncate ${
                result.connected ? 'text-content-positive-soft' : 'text-content-negative-soft'
              }`}
            >
              {result.connected ? 'Connected' : 'Connection failed'}
              {version ? ` · ${version}` : ''}
            </Text>
            <Show when={!!result.error}>
              <Text level="caption" className="text-content-negative-soft">
                {result.error}
              </Text>
            </Show>
          </VStack>
        </HStack>

        <HStack className="gap-2 items-center shrink-0">
          <Show when={!!result.target}>
            <Text level="caption" className="text-content-layout-3">
              {result.target}
            </Text>
          </Show>
          <Show when={!!onDismiss}>
            <button
              type="button"
              title="Dismiss test result"
              aria-label="Dismiss test result"
              onClick={onDismiss}
              className="flex items-center justify-center h-6 w-6 rounded-lg text-content-layout-3 hover:text-content-layout-1 hover:bg-surface-layout-2 transition-colors cursor-pointer"
            >
              <Icon name="close" label="Dismiss" className="w-3.5 h-3.5" />
            </button>
          </Show>
        </HStack>
      </div>
    </m.div>
  );
}
