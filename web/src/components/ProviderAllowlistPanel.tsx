import { Button } from '@rs/ui-new/button'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Icon } from '@rs/ui-new/icon'
import { Spinner } from '@rs/ui-new/spinner'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'
import {
  addCurrentIpToAllowlist,
  fetchAllowlistContext,
} from '../lib/allowlist'

type Provider = 'supabase' | 'neon' | 'digitalocean'

const PROVIDER_LABEL: Record<Provider, string> = {
  supabase: 'Supabase',
  neon: 'Neon',
  digitalocean: 'DigitalOcean',
}

function providerLabel(provider: string): string {
  return PROVIDER_LABEL[provider as Provider] ?? provider
}

export function ProviderAllowlistPanel({
  target,
  onAdded,
  onTestAgain,
}: {
  target: string
  onAdded?: () => void
  onTestAgain?: () => Promise<boolean>
}) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [retesting, setRetesting] = useState(false)
  const [retestError, setRetestError] = useState<string | null>(null)
  const {
    data: context,
    error: contextError,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ['allowlist-context', target],
    queryFn: () => fetchAllowlistContext(target),
    retry: false,
    staleTime: 0,
  })
  const addMutation = useMutation({
    mutationFn: () =>
      addCurrentIpToAllowlist(target, context?.current_ip ?? ''),
    onSuccess: async () => {
      await refetch()
    },
  })

  const testAgain = async () => {
    if (!onTestAgain) {
      setConfirmOpen(false)
      onAdded?.()
      return
    }
    setRetesting(true)
    setRetestError(null)
    try {
      if (await onTestAgain()) {
        setConfirmOpen(false)
        onAdded?.()
      } else {
        setRetestError(
          'The connection is still blocked. Review the latest error and try again.'
        )
      }
    } catch (caught) {
      setRetestError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setRetesting(false)
    }
  }

  if (isLoading) {
    return (
      <HStack className="gap-2 items-center rounded-lg border border-border-layout-1 bg-surface-layout-2/40 px-3 py-2">
        <Spinner size="base" />
        <Text level="caption" className="text-content-layout-3">
          Checking this machine&apos;s public IP and provider allowlist…
        </Text>
      </HStack>
    )
  }

  if (contextError || !context) {
    return (
      <div className="rounded-lg border border-border-warning-soft bg-surface-warning-soft/15 px-3 py-2">
        <Text level="caption" className="text-content-warning-soft">
          {contextError instanceof Error
            ? contextError.message
            : 'Could not load provider allowlist guidance.'}
        </Text>
      </div>
    )
  }

  const label = providerLabel(context.provider)
  const canConfirm =
    context.signed_in &&
    context.entry_count != null &&
    !context.already_allowed &&
    !context.error
  const cidr = `${context.current_ip}/32`

  return (
    <>
      <div className="rounded-lg border border-border-warning-soft bg-surface-warning-soft/15 px-3 py-2">
        <VStack className="gap-2 items-stretch">
          <HStack className="gap-2 items-center flex-wrap">
            <Icon
              name="connect"
              label=""
              aria-hidden="true"
              className="h-4 w-4 text-content-warning-soft"
            />
            <Text level="label-small" className="text-content-layout-1">
              Provider IP allowlist
            </Text>
            <Tag
              size="small"
              variant="neutral"
              modifier="ghost"
              label={context.current_ip}
            />
            {context.already_allowed && (
              <Tag
                size="small"
                variant="positive"
                modifier="ghost"
                label="Already allowed"
              />
            )}
          </HStack>
          <Text level="caption" className="text-content-layout-2">
            {context.guidance}
          </Text>
          {context.error && (
            <Text level="caption" className="text-content-warning-soft">
              {context.error} Load the current allowlist before adding this IP.
            </Text>
          )}
          {!context.signed_in && (
            <Link
              to="/configure"
              search={{ add: context.provider as Provider }}
              className="text-sm text-content-primary-solid hover:underline w-fit"
            >
              Connect {label}
            </Link>
          )}
          {canConfirm && (
            <Button
              variant="primary"
              modifier="solid"
              size="small"
              icon="add"
              iconPosition="left"
              label={`Add ${context.current_ip} to ${label} allowlist`}
              onClick={() => {
                addMutation.reset()
                setRetestError(null)
                setConfirmOpen(true)
              }}
            />
          )}
          {addMutation.data && (
            <Text level="caption" className="text-content-positive-soft">
              {addMutation.data.message}
            </Text>
          )}
          {addMutation.error && (
            <Text level="caption" className="text-content-negative-soft">
              {addMutation.error instanceof Error
                ? addMutation.error.message
                : String(addMutation.error)}
            </Text>
          )}
        </VStack>
      </div>
      <ConfirmDialog
        isOpen={confirmOpen}
        onClose={() => {
          setConfirmOpen(false)
          setRetestError(null)
        }}
        onConfirm={() => {
          if (addMutation.data?.verified) void testAgain()
          else if (!addMutation.isPending) addMutation.mutate()
        }}
        title={`Add ${context.current_ip} to the ${label} allowlist?`}
        subtitle={`Target: ${target}`}
        notice={{
          accent: 'warning',
          icon: 'connect',
          title: 'Allowlist change',
          message: `Add ${cidr}; keep ${context.entry_count} existing entries.`,
        }}
        confirmLabel={
          addMutation.data?.verified
            ? retesting
              ? 'Testing connection…'
              : 'Test connection again'
            : addMutation.isPending
              ? 'Adding IP…'
              : addMutation.error
                ? 'Try adding again'
                : 'Add IP to allowlist'
        }
        confirmVariant="primary"
        confirmIcon="add"
        confirmDisabled={addMutation.isPending || retesting}
        cancelLabel={addMutation.data?.verified ? 'Close' : 'Cancel'}
      >
        {addMutation.data && (
          <Text level="body-small" className="text-content-positive-soft">
            {addMutation.data.message}{' '}
            {addMutation.data.verified
              ? 'Provider confirmed the new entry.'
              : 'Provider did not confirm the new entry.'}
          </Text>
        )}
        {addMutation.error && (
          <Text level="body-small" className="text-content-negative-soft">
            {addMutation.error instanceof Error
              ? addMutation.error.message
              : String(addMutation.error)}
          </Text>
        )}
        {retestError && (
          <Text level="body-small" className="text-content-negative-soft">
            {retestError}
          </Text>
        )}
      </ConfirmDialog>
    </>
  )
}
