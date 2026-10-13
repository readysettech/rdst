import { createFileRoute } from '@tanstack/react-router'
// The page lives in the route-ignored `-sign-in-page` sibling so tests render
// it directly.
import { SignInPage } from './-sign-in-page'

type AccountLoginSearch = {
  login_id?: string
}

export const Route = createFileRoute('/account-login')({
  validateSearch: (search): AccountLoginSearch => ({
    login_id: typeof search.login_id === 'string' ? search.login_id : undefined,
  }),
  staticData: { layout: 'bare' },
  component: AccountLoginPage,
})

function AccountLoginPage() {
  const { login_id: loginId } = Route.useSearch()
  // A provider redirect lands here with its login id; the sign-in column finishes it.
  return <SignInPage callbackLoginId={loginId} />
}
