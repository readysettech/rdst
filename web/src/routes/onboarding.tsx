import { createFileRoute } from '@tanstack/react-router';
import { ConnectPage } from '../components/onboarding/ConnectPage';

// `redirect` preserves the destination the user was headed to when
// ConfigWarning routed them here, so connecting returns them there (not always
// Home). Parse-only — never throw (keeps the shell intact; B1 lesson).
type OnboardingSearch = { redirect?: string };

export const Route = createFileRoute('/onboarding')({
  validateSearch: (search: Record<string, unknown>): OnboardingSearch => ({
    redirect: typeof search.redirect === 'string' ? search.redirect : undefined,
  }),
  component: OnboardingPage,
});

function OnboardingPage() {
  // First run is now a single, exitable "Connect your database" page — the
  // four-step `fixed inset-0` wizard is retired (onboarding-and-first-run).
  const { redirect } = Route.useSearch();
  return <ConnectPage redirectTo={redirect} />;
}
