import { useCallback, useRef, useState } from 'react';
import { api } from './client';
import type { components } from './api.generated';
import type { OnboardingStep } from '../types/onboarding';

type InitStatus = components['schemas']['InitStatusResponse'];
type ValidationResult = components['schemas']['InitValidateResponse'];

interface UseOnboardingReturn {
  step: OnboardingStep;
  setStep: (step: OnboardingStep) => void;
  status: InitStatus | null;
  validationResults: ValidationResult | null;
  error: string | null;
  loading: boolean;
  checkStatus: () => Promise<InitStatus | null>;
  runValidation: (targetNames?: string[]) => Promise<ValidationResult | null>;
  completeInit: () => Promise<boolean>;
}

// Routes have no error responses declared, so openapi-fetch's error branch
// types as `never` and narrowing on `!data` collapses to an unreachable case.
// Reading `response.ok` directly off the result keeps `Response` in scope.
async function throwIfNotOk(response: Response): Promise<void> {
  if (response.ok) return;
  const body = await response.text().catch(() => '');
  throw new Error(`HTTP error! status: ${response.status}, body: ${body}`);
}

export function useOnboarding(): UseOnboardingReturn {
  const [step, setStep] = useState<OnboardingStep>('welcome');
  const [status, setStatus] = useState<InitStatus | null>(null);
  const [validationResults, setValidationResults] = useState<ValidationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Wizard operations cancel each other — reuse a single AbortController so
  // e.g. switching steps mid-validation drops the stale request.
  const abortControllerRef = useRef<AbortController | null>(null);

  const newController = () => {
    abortControllerRef.current?.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    return controller;
  };

  const finish = (controller: AbortController) => {
    if (abortControllerRef.current === controller) {
      setLoading(false);
      abortControllerRef.current = null;
    }
  };

  const checkStatus = useCallback(async () => {
    const controller = newController();
    setLoading(true);
    setError(null);
    try {
      const result = await api.GET('/api/init/status', { signal: controller.signal });
      await throwIfNotOk(result.response);
      if (!result.data) throw new Error('Missing response body');
      setStatus(result.data);
      return result.data;
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return null;
      setError(err instanceof Error ? err.message : 'Failed to load init status');
      return null;
    } finally {
      finish(controller);
    }
  }, []);

  const runValidation = useCallback(async (targetNames?: string[]) => {
    const controller = newController();
    setLoading(true);
    setError(null);
    try {
      const result = await api.POST('/api/init/validate', {
        body: { targets: targetNames ?? null },
        signal: controller.signal,
      });
      await throwIfNotOk(result.response);
      if (!result.data) throw new Error('Missing response body');
      setValidationResults(result.data);
      return result.data;
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return null;
      setError(err instanceof Error ? err.message : 'Validation failed');
      return null;
    } finally {
      finish(controller);
    }
  }, []);

  const completeInit = useCallback(async () => {
    const controller = newController();
    setLoading(true);
    setError(null);
    try {
      const result = await api.POST('/api/init/complete', { signal: controller.signal });
      await throwIfNotOk(result.response);
      if (!result.data) throw new Error('Missing response body');
      const ok = Boolean(result.data.success);
      if (!ok) setError('Failed to complete init');
      return ok;
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return false;
      setError(err instanceof Error ? err.message : 'Failed to complete init');
      return false;
    } finally {
      finish(controller);
    }
  }, []);

  return {
    step,
    setStep,
    status,
    validationResults,
    error,
    loading,
    checkStatus,
    runValidation,
    completeInit,
  };
}
