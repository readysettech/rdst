import { useCallback, useRef, useState } from 'react';
import type { InitStatus, ValidationResult, OnboardingStep } from '../types/onboarding';

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

export function useOnboarding(): UseOnboardingReturn {
  const [step, setStep] = useState<OnboardingStep>('welcome');
  const [status, setStatus] = useState<InitStatus | null>(null);
  const [validationResults, setValidationResults] = useState<ValidationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const checkStatus = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;
    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/init/status', { signal: controller.signal });
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
      }
      const data: InitStatus = await response.json();
      setStatus(data);
      return data;
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return null;
      }
      setError(err instanceof Error ? err.message : 'Failed to load init status');
      return null;
    } finally {
      if (abortControllerRef.current === controller) {
        setLoading(false);
        abortControllerRef.current = null;
      }
    }
  }, []);

  const runValidation = useCallback(async (targetNames?: string[]) => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;
    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/init/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ targets: targetNames }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
      }
      const data: ValidationResult = await response.json();
      setValidationResults(data);
      return data;
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return null;
      }
      setError(err instanceof Error ? err.message : 'Validation failed');
      return null;
    } finally {
      if (abortControllerRef.current === controller) {
        setLoading(false);
        abortControllerRef.current = null;
      }
    }
  }, []);

  const completeInit = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;
    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/init/complete', {
        method: 'POST',
        signal: controller.signal,
      });
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP error! status: ${response.status}, body: ${errorText}`);
      }
      const data: { success: boolean } = await response.json();
      const ok = Boolean(data.success);
      if (!ok) {
        setError('Failed to complete init');
      }
      return ok;
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return false;
      }
      setError(err instanceof Error ? err.message : 'Failed to complete init');
      return false;
    } finally {
      if (abortControllerRef.current === controller) {
        setLoading(false);
        abortControllerRef.current = null;
      }
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
