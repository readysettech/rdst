// Shared SSE frame reader. Parses `event:`/`data:` frames from a streamed
// response and invokes the callback per data frame; unparseable data lines
// (keep-alives, partial frames) are skipped.

export async function consumeSseResponse(
  response: Response,
  onEvent: (event: string, data: unknown) => void
): Promise<void> {
  if (!response.body) throw new Error('no response body')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = 'message'

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) {
        currentEvent = 'message'
        continue
      }
      if (trimmed.startsWith('event:')) {
        currentEvent = trimmed.substring(6).trim()
      } else if (trimmed.startsWith('data:')) {
        try {
          onEvent(currentEvent, JSON.parse(trimmed.substring(5).trim()))
        } catch {
          /* ignore keep-alive / partial frames */
        }
      }
    }
  }
}

export async function readSSE(
  url: string,
  init: RequestInit,
  onEvent: (event: string, data: unknown) => void,
  signal: AbortSignal
): Promise<void> {
  const response = await fetch(url, { ...init, signal })
  if (!response.ok)
    throw new Error((await response.text()) || response.statusText)
  await consumeSseResponse(response, onEvent)
}
