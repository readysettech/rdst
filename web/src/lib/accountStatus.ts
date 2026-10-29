import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { type AccountStatusResponse, fetchAccountStatus } from './api'
import { invalidateTrialRelatedQueries } from './trialQueries'

// A sign-in can finish outside this window, from the emailed link, while this window is in
// the background, so a signed-out app checks every few seconds there too and again on return;
// while signed out the check stays local.
const SIGNED_OUT_POLL_MS = 3_000

export function useAccountStatus() {
  return useQuery({
    queryKey: ['account-status'],
    queryFn: fetchAccountStatus,
    staleTime: 60_000,
    refetchInterval: (query) =>
      query.state.data?.signed_in === false ? SIGNED_OUT_POLL_MS : false,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: (query) =>
      query.state.data?.signed_in === false ? 'always' : true,
  })
}

/**
 * The account status for the app shell. A sign-in that finishes outside this window, from
 * the emailed link, also refreshes the AI gates and the sidebar, which read it through
 * their own queries.
 */
export function useAccountStatusWatch() {
  const queryClient = useQueryClient()
  const status = useAccountStatus()
  const signedIn = status.data?.signed_in
  const wasSignedIn = useRef(signedIn)
  useEffect(() => {
    if (wasSignedIn.current === false && signedIn)
      void invalidateTrialRelatedQueries(queryClient)
    wasSignedIn.current = signedIn
  }, [signedIn, queryClient])
  return status
}

/** Signed in to a Readyset account. */
export function isSignedInAccount(
  status: AccountStatusResponse | undefined
): boolean {
  return Boolean(status?.signed_in)
}

/** True once the account status says nobody is signed in; false while it loads. */
export function useSignedOut(): boolean {
  const { data } = useAccountStatus()
  return data ? !data.signed_in : false
}
