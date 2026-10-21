import { useQuery } from '@tanstack/react-query'
import { type AccountStatusResponse, fetchAccountStatus } from './api'

export function useAccountStatus() {
  return useQuery({
    queryKey: ['account-status'],
    queryFn: fetchAccountStatus,
    staleTime: 60_000,
  })
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
