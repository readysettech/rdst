import { Dropdown } from '@rs/ui-new/dropdown'
import { focusRingClass } from '@rs/ui-new/focus'
import { Pressable } from '@rs/ui-new/pressable'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { fetchAccountStatus, logoutAccount } from '../lib/api'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'

const lineClass =
  'flex w-full items-center rounded-control px-2.5 py-2 text-left text-caption text-content-layout-3 hover:bg-surface-layout-1 hover:text-content-layout-1 data-[state=open]:bg-surface-layout-1 data-[state=open]:text-content-layout-1'

const itemClass = 'h-8.5 min-w-0 rounded-md px-2.5 text-body-small'

/**
 * The account line at the bottom of the sidebar. The email opens the account
 * menu: whose account it is, Settings, Help, and Sign out alone under a
 * hairline. Signed out, the line is the way to the sign-in page.
 */
export function SidebarAccountMenu() {
  const { data } = useQuery({
    queryKey: ['account-status'],
    queryFn: fetchAccountStatus,
    staleTime: 60_000,
  })
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)

  const signOut = useMutation({
    mutationFn: logoutAccount,
    onSuccess: async () => {
      await invalidateTrialRelatedQueries(queryClient)
      void navigate({ to: '/account-login' })
    },
  })

  const email = data?.signed_in ? data.email || 'Signed in' : null
  if (!email)
    return (
      <Link
        to="/account-login"
        data-testid="sidebar-sign-in"
        className={`${lineClass} ${focusRingClass}`}
      >
        Sign in
      </Link>
    )

  const go = (to: '/configure' | '/help') => {
    setMenuOpen(false)
    void navigate({ to })
  }

  return (
    <Dropdown open={menuOpen} onOpenChange={setMenuOpen}>
      <Dropdown.Trigger asChild>
        <Pressable
          data-testid="sidebar-account-email"
          aria-label={`Account: ${email}`}
          className={lineClass}
        >
          <span className="truncate">{email}</span>
        </Pressable>
      </Dropdown.Trigger>
      <Dropdown.Content
        align="start"
        side="top"
        className="w-55 min-w-55 rounded-panel bg-surface-primary-soft p-1.5"
      >
        <Dropdown.Label className="border-b-0 px-2.5 pt-1.5 pb-1 text-caption font-normal">
          {email}
        </Dropdown.Label>
        <Dropdown.Item
          label="Settings"
          className={itemClass}
          onClick={() => go('/configure')}
        />
        <Dropdown.Item
          label="Help"
          className={itemClass}
          onClick={() => go('/help')}
        />
        <Dropdown.Separator className="-mx-1.5" />
        <Dropdown.Item
          label="Sign out"
          className={itemClass}
          disabled={signOut.isPending}
          onClick={() => signOut.mutate()}
        />
      </Dropdown.Content>
    </Dropdown>
  )
}
