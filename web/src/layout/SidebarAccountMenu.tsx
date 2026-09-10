import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Dropdown } from '@rs/ui-new/dropdown'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { useToast } from '@rs/ui-new/use-toast'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { fetchAccountStatus, logoutAccount } from '../lib/api'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'

/**
 * The signed-in account, as a control.
 *
 * The email in the sidebar footer is where a user looks for their account, so
 * it opens the account menu: the address itself, the way to the AI access
 * settings, and Sign out behind a confirmation. Settings keeps its own sign-out
 * as the secondary path. [Mike #7 / A-09]
 */
export function SidebarAccountMenu({
  onNavigate,
}: {
  onNavigate?: () => void
}) {
  const { data } = useQuery({
    queryKey: ['account-status'],
    queryFn: fetchAccountStatus,
    staleTime: 60_000,
  })
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { toast } = useToast()
  const [menuOpen, setMenuOpen] = useState(false)
  const [confirmingSignOut, setConfirmingSignOut] = useState(false)

  const signOut = useMutation({
    mutationFn: logoutAccount,
    onSuccess: async () => {
      setConfirmingSignOut(false)
      await invalidateTrialRelatedQueries(queryClient)
      toast({
        title: 'Signed out of Readyset',
        description:
          'Add an Anthropic key or sign in again to use AI features.',
        variant: 'positive',
      })
    },
  })

  const email = data?.signed_in ? data.email : null
  if (!email) return null

  return (
    <div className="px-1">
      <Dropdown open={menuOpen} onOpenChange={setMenuOpen}>
        <Dropdown.Trigger asChild>
          <Pressable
            type="button"
            data-testid="sidebar-account-email"
            aria-label={`Account: ${email}`}
            className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1 text-left cursor-pointer hover:bg-surface-layout-2 focus-visible:shadow-focus focus-visible:outline-none"
          >
            <Text
              as="div"
              level="caption"
              className="truncate text-content-layout-3"
            >
              {email}
            </Text>
            <Icon
              name="chevron-down"
              label=""
              size="small"
              className="shrink-0 text-content-layout-3"
            />
          </Pressable>
        </Dropdown.Trigger>
        <Dropdown.Content align="start" side="top" className="min-w-64">
          <Dropdown.Label>Signed in as {email}</Dropdown.Label>
          <Dropdown.Item
            label="Manage AI access"
            leftIcon="sparkles"
            onClick={() => {
              setMenuOpen(false)
              onNavigate?.()
              void navigate({ to: '/configure', search: { panel: 'ai' } })
            }}
          />
          <Dropdown.Item
            label="Sign out"
            leftIcon="logout"
            onClick={() => {
              setMenuOpen(false)
              setConfirmingSignOut(true)
            }}
          />
        </Dropdown.Content>
      </Dropdown>

      <ConfirmDialog
        isOpen={confirmingSignOut}
        onClose={() => setConfirmingSignOut(false)}
        onConfirm={() => signOut.mutate()}
        title="Sign out of Readyset?"
        subtitle={email}
        notice={{
          accent: 'warning',
          icon: 'alert',
          message:
            'Analyze, Ask and Health check lose their AI insights until you sign in again or add an Anthropic key.',
        }}
        confirmLabel="Sign out"
        confirmIcon="logout"
        // Signing back in restores everything, so this is not a red confirm.
        confirmVariant="primary"
        loading={signOut.isPending}
        blockCloseWhileLoading
      />
    </div>
  )
}
