/**
 * Per-row connectivity for a configured database target.
 *
 * The compact cell carries the round-trip latency plus a condensed server
 * banner; an unreachable target additionally grows a persistent notice that
 * leads with the first line of the failure and keeps the raw text behind a
 * Details expander. A password failure is recognised from three signals (no
 * stored password, the TARGET_PASSWORD_REQUIRED code, or the message itself)
 * and offers the password action instead of the driver text.
 */

import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useState } from 'react'
import type { FleetConnectivityEvent } from '../../types/fleet'

/** The row fields the connectivity surfaces read. */
export interface ConnectivityTarget {
  name: string
  has_password?: boolean
}

/**
 * Condense the raw server banner to product + version.
 * "PostgreSQL 17.10 (Debian 17.10-1) on aarch64-..." becomes "PostgreSQL 17.10".
 */
export function condenseServerVersion(raw?: string | null): string | null {
  if (!raw) return null
  const head = raw.split(/[(,]/)[0]?.trim() ?? ''
  if (!head) return null
  return head.length > 60 ? `${head.slice(0, 57)}…` : head
}

/** True once a check has settled on a failure for this target. */
export function isUnreachable(
  result: FleetConnectivityEvent | undefined
): boolean {
  return !!result && result.status !== 'ok' && result.status !== 'checking'
}

export function TargetConnectivity({
  result,
}: {
  result: FleetConnectivityEvent | undefined
}) {
  if (!result) {
    return (
      <Text level="caption" className="text-content-layout-3">
        -
      </Text>
    )
  }
  if (result.status === 'checking') {
    return <Spinner size="base" />
  }
  if (result.status === 'ok') {
    const version = condenseServerVersion(result.server_version)
    return (
      <VStack className="gap-0.5 items-end">
        <Tag
          size="small"
          variant="positive"
          modifier="ghost"
          label={`${result.latency_ms}ms`}
        />
        {version && (
          <Text
            level="caption"
            className="text-content-layout-3 truncate max-w-48"
          >
            {version}
          </Text>
        )}
      </VStack>
    )
  }
  return (
    <Tag size="small" variant="negative" modifier="ghost" label="Unreachable" />
  )
}

export function UnreachableNotice({
  target,
  result,
  onSetPassword,
}: {
  target: ConnectivityTarget
  result: FleetConnectivityEvent | undefined
  onSetPassword?: (target: ConnectivityTarget) => void
}) {
  const [detailsOpen, setDetailsOpen] = useState(false)
  const error = result?.error ?? ''
  const firstLine =
    error.split('\n')[0] || 'The last connectivity check failed.'
  const passwordIssue =
    target.has_password === false ||
    result?.code === 'TARGET_PASSWORD_REQUIRED' ||
    /password/i.test(error)

  return (
    <div className="rounded-lg border border-border-warning-soft bg-surface-warning-soft/15 px-3 py-2">
      <HStack className="gap-2 items-center flex-wrap">
        <Icon
          name="alert"
          label=""
          aria-hidden="true"
          className="w-4 h-4 text-content-warning-soft shrink-0"
        />
        <Text level="caption" className="text-content-layout-2 flex-1 min-w-48">
          {passwordIssue
            ? `Enter the password for '${target.name}' again.`
            : firstLine}
        </Text>
        {passwordIssue && onSetPassword && (
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            label="Set password"
            icon="key"
            iconPosition="left"
            onClick={() => onSetPassword(target)}
          />
        )}
        {error && !passwordIssue && (
          <button
            type="button"
            onClick={() => setDetailsOpen((open) => !open)}
            className="cursor-pointer"
          >
            <Text
              level="caption"
              className="text-content-layout-3 hover:underline"
            >
              {detailsOpen ? 'Hide details' : 'Details'}
            </Text>
          </button>
        )}
      </HStack>
      {detailsOpen && error && !passwordIssue && (
        <pre className="mt-2 text-xs text-content-layout-3 whitespace-pre-wrap break-all">
          {error}
        </pre>
      )}
    </div>
  )
}
