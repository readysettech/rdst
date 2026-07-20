import { createFileRoute } from '@tanstack/react-router';
import { ConnectPage } from '../components/onboarding/ConnectPage';

// `redirect` preserves the destination the user was headed to when
// ConfigWarning routed them here, so connecting returns them there (not always
// Home). `from=demo` marks the demo→conviction hand-off so Connect can
// acknowledge it in one line. Parse-only — never throw (keeps the shell intact;
// B1 lesson).
type OnboardingSearch = { redirect?: string; from?: string };

export const Route = createFileRoute('/onboarding')({
  validateSearch: (search: Record<string, unknown>): OnboardingSearch => ({
    redirect: typeof search.redirect === 'string' ? search.redirect : undefined,
    from: typeof search.from === 'string' ? search.from : undefined,
  }),
  component: OnboardingPage,
});

function OnboardingPage() {
  // First run is now a single, exitable "Connect your database" page — the
  // four-step `fixed inset-0` wizard is retired (onboarding-and-first-run).
  const { redirect, from } = Route.useSearch();
  return <ConnectPage redirectTo={redirect} from={from} />;
}
