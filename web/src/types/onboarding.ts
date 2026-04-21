// Onboarding step names are UI-only (not in the backend API).
export type OnboardingStep = 'welcome' | 'targets' | 'validate' | 'complete';

// Backend-generated types, re-exported under their historical names so
// existing consumers don't have to change their import path.
import type { components } from '../lib/api.generated';

export type InitStatus = components['schemas']['InitStatusResponse'];
export type ValidationResult = components['schemas']['InitValidateResponse'];
