import type { BrowserWindow } from 'electron'

async function smokeStep<T>(
  operation: Promise<T>,
  description: string
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([
      operation,
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => reject(new Error(`Smoke step timed out: ${description} (15s)`)),
          15_000
        )
      }),
    ])
  } finally {
    clearTimeout(timer)
  }
}

/** Exercise the installed renderer and lazy backend modules through the renderer's proxy. */
export async function checkPackagedWorkflow(
  window: BrowserWindow,
  rendererUrl: string
): Promise<void> {
  const startedAt = Math.floor(Date.now() / 1000)
  const target = process.env.RDST_DESKTOP_SMOKE_TARGET
    ? JSON.parse(process.env.RDST_DESKTOP_SMOKE_TARGET)
    : {
        engine: 'postgresql',
        host: '127.0.0.1',
        port: 1,
        database: 'smoke',
        user: 'smoke',
      }
  const request = async (route: string, body?: unknown, method = 'GET') => {
    const result = await smokeStep(
      window.webContents.executeJavaScript(`(async () => {
      const response = await fetch(${JSON.stringify(`/api/${route}`)}, {
        method: ${JSON.stringify(method)}, headers: {'Content-Type': 'application/json'},
        body: ${body === undefined ? 'undefined' : JSON.stringify(JSON.stringify(body))}
      });
      if (!response.ok) throw new Error('Smoke API HTTP ' + response.status);
      return response.json();
    })()`),
      `API ${method} ${route}`
    )
    if (result.success === false) throw new Error(`Smoke API failed: ${route}`)
    return result
  }
  const configured = await request('configure/targets')
  if (
    !configured.targets.some(
      (entry: { name: string }) => entry.name === 'desktop-smoke'
    )
  ) {
    await request(
      'configure/targets',
      { name: 'desktop-smoke', target },
      'POST'
    )
  }
  await request(
    'monitor/targets/desktop-smoke/settings',
    { enabled: true },
    'PUT'
  )
  await smokeStep(
    window.loadURL(`${rendererUrl}/database?target=desktop-smoke`),
    'Database navigation'
  )
  const deadline = Date.now() + 60_000
  let rendered = false
  while (Date.now() < deadline) {
    rendered = await smokeStep(
      window.webContents.executeJavaScript(`Array.from(document.querySelectorAll('h1,h2'))
      .some(node => node.textContent.includes('desktop-smoke') && node.getBoundingClientRect().height > 0)`),
      'Database heading'
    )
    if (rendered) break
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  if (!rendered)
    throw new Error('Installed React Database heading did not render')
  await request('monitor/targets/desktop-smoke/health')
  await request('monitor/targets/desktop-smoke/queries')
  await request('monitor/work')
  let pulse = await request('monitor/targets/desktop-smoke/pulse')
  if (process.env.RDST_DESKTOP_SMOKE_TARGET) {
    const collected = () =>
      pulse.pulses?.some(
        (entry: { kind: string; ok: boolean; ts: number }) =>
          entry.kind === 'collect' && entry.ok && entry.ts >= startedAt
      )
    const collectionDeadline = Date.now() + 60_000
    while (!collected() && Date.now() < collectionDeadline) {
      await new Promise((resolve) => setTimeout(resolve, 500))
      pulse = await request('monitor/targets/desktop-smoke/pulse')
    }
    if (!collected())
      throw new Error('Packaged collector produced no successful reading')
    console.log('SMOKE_READING_OK')
  } else {
    console.log(
      'SMOKE_API_ONLY: set RDST_DESKTOP_SMOKE_TARGET for a live collection check'
    )
  }
  console.log('SMOKE_WORKFLOW_OK')
}
