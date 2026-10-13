// AI checks wait for a Readyset sign-in on a monitored database whose monitor settings say
// it's signed out. One reading for every page that explains the hold, and one line saying so.
import { cn } from '@rs/tailwind-base'
import { Text } from '@rs/ui-new/text'
import { useQueries } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { getSettings } from '@/lib/monitor/api'
import { monitorKeys } from '@/lib/monitor/keys'
import type { MonitorSettings } from '@/lib/monitor/types'

export const SIGN_IN_TO_RUN = 'Sign in to run AI checks.'

/** Monitoring is on and its AI checks wait for a sign-in. */
export const heldForSignIn = (settings: MonitorSettings | undefined): boolean =>
  Boolean(settings?.enabled) && settings?.ai_reason === 'signed_out'

/** The databases among `targets` whose AI checks wait for a sign-in. */
export function useSignInHeld(targets: readonly string[]): ReadonlySet<string> {
  return useQueries({
    queries: targets.map((target) => ({
      queryKey: monitorKeys.settings(target),
      queryFn: () => getSettings(target),
    })),
    combine: (results) =>
      new Set(
        results.flatMap((result, index) =>
          heldForSignIn(result.data) ? [targets[index]] : []
        )
      ),
  })
}

/** "Sign in to run AI checks. Sign in", one caption line. */
export function SignInNote({ className }: { className?: string }) {
  return (
    <Text
      as="p"
      level="caption"
      className={cn('text-content-layout-3', className)}
      data-testid="sign-in-note"
    >
      {SIGN_IN_TO_RUN}{' '}
      <Link
        to="/account-login"
        className="border-b border-border-layout-2 text-content-layout-2 hover:border-content-layout-2"
      >
        Sign in
      </Link>
    </Text>
  )
}
