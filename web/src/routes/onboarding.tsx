import { createFileRoute } from '@tanstack/react-router';
import { OnboardingWizard } from '../components/onboarding/OnboardingWizard';

export const Route = createFileRoute('/onboarding')({
  component: OnboardingPage,
});

function OnboardingPage() {
  return <OnboardingWizard />;
}
