import { useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { monitorKeys } from './monitor/keys'

/** Refetches monitoring after the desktop tray changes it, so open pages follow. */
export function useDesktopMonitoringChanges(): void {
  const client = useQueryClient()
  useEffect(
    () =>
      window.rdstDesktop?.monitoring?.onChange(() => {
        void client.invalidateQueries({ queryKey: monitorKeys.all })
      }),
    [client]
  )
}
