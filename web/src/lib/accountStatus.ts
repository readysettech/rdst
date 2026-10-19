import { useQuery } from '@tanstack/react-query'
import { type AccountStatusResponse, fetchAccountStatus } from './api'

/**
 * How hard RDST asks for a sign-in. Without an account, hosted AI runs on a free
 * allowance per computer: `soft` asks after a week, `budget` once most of it is used,
 * `required` once it has ended (signed-in and own-key users never see it).
 */
export type SigninPrompt = NonNullable<AccountStatusResponse['signin_prompt']>

export function useAccountStatus() {
  return useQuery({
    queryKey: ['account-status'],
    queryFn: fetchAccountStatus,
    staleTime: 60_000,
  })
}

/** The prompt the backend asks for; null from a backend without free hosted AI. */
export function signinPromptOf(
  status: AccountStatusResponse | undefined
): SigninPrompt | null {
  return status?.signin_prompt ?? null
}

/** Signed in to a Readyset account, not the anonymous one hosted AI starts on. */
export function isSignedInAccount(
  status: AccountStatusResponse | undefined
): boolean {
  return Boolean(status?.signed_in && !status.is_anonymous)
}

/**
 * True when a feature that runs through Readyset must ask for a sign-in now: the free
 * allowance has ended, or the backend has none and nobody is signed in.
 */
export function useSigninRequired(): boolean {
  const { data } = useAccountStatus()
  if (!data) return false
  const prompt = signinPromptOf(data)
  return prompt === null ? !data.signed_in : prompt === 'required'
}
