import { createFileRoute, useNavigate } from '@tanstack/react-router'
import {
  AccountLoginDialog,
  takeLoginReturn,
} from '../components/TrialRegistrationDialog'

type AccountLoginSearch = {
  login_id?: string
}

export const Route = createFileRoute('/account-login')({
  validateSearch: (search): AccountLoginSearch => ({
    login_id: typeof search.login_id === 'string' ? search.login_id : undefined,
  }),
  component: AccountLoginPage,
})

function AccountLoginPage() {
  const navigate = useNavigate()
  const { login_id: loginId } = Route.useSearch()

  return (
    <AccountLoginDialog
      isOpen
      callbackLoginId={loginId}
      onClose={() => void navigate({ to: '/' })}
      // Back to the page sign-in started on (the Database page, a dialog's page).
      onSuccess={() => void navigate({ href: takeLoginReturn() })}
    />
  )
}
