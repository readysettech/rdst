import vm from 'node:vm'
import type { BrowserWindow } from 'electron'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { checkPackagedWorkflow } from './packaged-smoke.js'

function harness(failure?: string) {
  const requests: string[] = []
  const fetch = vi.fn(async (route: string) => {
    requests.push(route)
    return {
      ok: route !== failure,
      status: route === failure ? 500 : 200,
      json: async () =>
        route === '/api/configure/targets'
          ? { targets: [], success: true }
          : {
              enabled: true,
              pulses: [
                {
                  kind: 'collect',
                  ok: true,
                  ts: Math.floor(Date.now() / 1000),
                },
              ],
            },
    }
  })
  const window = {
    loadURL: vi.fn(async () => {}),
    webContents: {
      executeJavaScript: vi.fn(async (script: string) => {
        if (script.includes('querySelectorAll')) return true
        return vm.runInNewContext(script, { fetch })
      }),
    },
  }
  return { window, requests }
}

afterEach(() => {
  vi.unstubAllEnvs()
  vi.useRealTimers()
})
describe('installed Database workflow', () => {
  it('loads React and exercises configure, enable, health, queries, work and a real reading', async () => {
    vi.stubEnv(
      'RDST_DESKTOP_SMOKE_TARGET',
      JSON.stringify({ host: '127.0.0.1' })
    )
    const { window, requests } = harness()
    await checkPackagedWorkflow(
      window as unknown as BrowserWindow,
      'http://127.0.0.1:9000'
    )
    expect(window.loadURL).toHaveBeenCalledWith(
      'http://127.0.0.1:9000/database?target=desktop-smoke'
    )
    expect(requests).toContain('/api/monitor/targets/desktop-smoke/queries')
    expect(requests).toContain('/api/monitor/targets/desktop-smoke/pulse')
    expect(requests).toContain('/api/monitor/work')
  })

  it('fails a stalled renderer request with the route and deadline', async () => {
    vi.useFakeTimers()
    const { window } = harness()
    window.webContents.executeJavaScript.mockImplementation(
      () => new Promise(() => {})
    )
    const check = checkPackagedWorkflow(
      window as unknown as BrowserWindow,
      'http://localhost'
    )
    const rejected = expect(check).rejects.toThrow(
      'Smoke step timed out: API GET configure/targets (15s)'
    )
    await vi.advanceTimersByTimeAsync(15_000)
    await rejected
  })

  it('fails the package gate when a lazy route has a missing module', async () => {
    const { window } = harness('/api/monitor/targets/desktop-smoke/queries')
    await expect(
      checkPackagedWorkflow(
        window as unknown as BrowserWindow,
        'http://localhost'
      )
    ).rejects.toThrow('Smoke API HTTP 500')
  })
})
