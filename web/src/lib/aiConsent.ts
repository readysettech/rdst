import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  type AIDataConsent,
  fetchAIDataSettings,
  setAIDataConsent,
} from './api'
import { invalidateTrialRelatedQueries } from './trialQueries'

export const aiDataSettingsQueryKey = ['settings', 'ai-data'] as const

export interface AiConsent {
  /** This build sends nothing to AI until the user allows it (the Microsoft Store build). */
  required: boolean
  consent: AIDataConsent | undefined
  /** AI is off because the user hasn't allowed it: declined, or not asked yet. */
  off: boolean
  /** The user turned AI off; not asked yet doesn't count. */
  declined: boolean
  loading: boolean
  /** The saved choice couldn't be read. */
  failed: boolean
  decide: (consent: Exclude<AIDataConsent, 'pending'>) => Promise<void>
  deciding: boolean
  error: Error | null
  /** Forgets a failed save, for a dialog opened again. */
  resetError: () => void
}

/** Whether AI may run, and the one way to change it. */
export function useAiConsent(): AiConsent {
  const client = useQueryClient()
  const settings = useQuery({
    queryKey: aiDataSettingsQueryKey,
    queryFn: fetchAIDataSettings,
  })
  const update = useMutation({
    mutationFn: setAIDataConsent,
    onMutate: async () => {
      await client.cancelQueries({ queryKey: aiDataSettingsQueryKey })
    },
    onSuccess: async (data) => {
      // A read started while the save was in flight would otherwise land on top of it.
      await client.cancelQueries({ queryKey: aiDataSettingsQueryKey })
      client.setQueryData(aiDataSettingsQueryKey, data)
      // Monitoring states, queued analyses and classifications follow the choice. The AI
      // gate reads the choice itself; with AI on it also checks the provider again.
      if (data.consent === 'granted') void invalidateTrialRelatedQueries(client)
      else {
        void client.invalidateQueries({ queryKey: ['monitor'] })
        void client.invalidateQueries({ queryKey: ['status'] })
      }
    },
  })
  const required = settings.data?.consent_required === true
  const consent = settings.data?.consent
  return {
    required,
    consent,
    off: required && consent !== 'granted',
    declined: required && consent === 'denied',
    loading: settings.isLoading,
    failed: settings.isError,
    decide: async (choice) => {
      await update.mutateAsync(choice)
    },
    deciding: update.isPending,
    error: update.error,
    resetError: update.reset,
  }
}
