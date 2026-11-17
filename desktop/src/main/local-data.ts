import type { Session } from 'electron'

export async function clearRdstRendererStorage(
  storageSession: Pick<Session, 'clearStorageData' | 'clearCache'>
) {
  await storageSession.clearStorageData()
  await storageSession.clearCache()
}
