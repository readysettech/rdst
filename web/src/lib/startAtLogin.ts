import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

const startAtLoginQueryKey = ['desktop', 'start-at-login'] as const

/** The desktop app's Start at login, shared by Settings and the first-run choices. */
export function useStartAtLogin() {
  const client = useQueryClient()
  const desktop = window.rdstDesktop
  const status = useQuery({
    queryKey: startAtLoginQueryKey,
    queryFn: () => desktop?.getStartAtLogin?.() ?? null,
    enabled: desktop?.getStartAtLogin !== undefined,
  })
  const update = useMutation({
    mutationFn: async (enabled: boolean) => {
      if (!desktop?.setStartAtLogin)
        throw new Error('Start at login is unavailable')
      return desktop.setStartAtLogin(enabled)
    },
    onSuccess: (data) => client.setQueryData(startAtLoginQueryKey, data),
  })
  return { status, update }
}
