import type { ConfigureTarget } from './configure';

export interface InitStatus {
  initialized: boolean;
  targets: ConfigureTarget[];
  default_target: string | null;
  llm_configured: boolean;
}

export interface ValidationResult {
  target_results: Array<{
    name: string;
    success: boolean;
    error?: string;
    version?: string;
  }>;
  llm_result: {
    success: boolean;
    error?: string;
    model?: string;
  };
}

export type OnboardingStep = 'welcome' | 'targets' | 'validate' | 'complete';
