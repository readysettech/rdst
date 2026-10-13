import type { BackendStartState } from './backend-supervisor.js'

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`)
}

interface PageCopy {
  title: string
  body: string
  busy: boolean
  error?: string
  retry?: boolean
  reload?: boolean
}

function copyFor(state: BackendStartState): PageCopy {
  switch (state.phase) {
    case 'migrating':
      return {
        title: 'Upgrading your data, this can take a minute',
        body:
          'RDST is updating its saved queries and monitoring history for this version. ' +
          "It'll open by itself when it's done. Please don't quit RDST until then.",
        busy: true,
      }
    case 'retrying':
      return {
        title: 'Restarting RDST',
        body:
          `RDST's background service stopped while starting. ` +
          `Trying again in ${Math.round(state.retryInMs / 1000)} seconds.`,
        busy: true,
        error: state.error,
      }
    case 'failed':
      return {
        title: "RDST couldn't start",
        body: 'Its background service stopped every time it started. The log says why.',
        busy: false,
        error: state.error,
        retry: true,
      }
    default:
      return { title: 'Starting RDST', body: '', busy: true }
  }
}

/** The page the window shows until the backend is ready, as a data: URL. */
export function startupPageHtml(state: BackendStartState): string {
  return pageHtml(copyFor(state), state.phase)
}

/** The page the window shows after its renderer kept crashing. */
export function rendererCrashPageHtml(): string {
  return pageHtml(
    {
      title: "RDST's window stopped working",
      body: 'It closed unexpectedly several times in a minute. The log may say why.',
      busy: false,
      reload: true,
    },
    'renderer-crashed'
  )
}

export function rendererCrashPageUrl(): string {
  return `data:text/html;charset=utf-8,${encodeURIComponent(rendererCrashPageHtml())}`
}

function pageHtml(copy: PageCopy, phase: string): string {
  const error = copy.error
    ? `<pre>${escapeHtml(copy.error.split('\n').slice(0, 30).join('\n'))}</pre>`
    : ''
  const buttons =
    copy.busy && !copy.error
      ? ''
      : `<div class="actions">
        ${copy.retry ? '<button id="retry" class="primary">Try again</button>' : ''}
        ${copy.reload ? '<button id="reload" class="primary">Reload</button>' : ''}
        <button id="logs">Open logs</button>
      </div>`
  return `<!doctype html>
<html><head><meta charset="utf-8"><title>RDST</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; height: 100vh; display: flex; align-items: center; justify-content: center;
    background: #171616; color: #e8e6e3; font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
    -webkit-app-region: drag; }
  main { max-width: 520px; padding: 24px; text-align: center; }
  h1 { font-size: 20px; font-weight: 600; margin: 16px 0 8px; }
  p { color: #b4b0ab; margin: 0 0 16px; }
  pre { text-align: left; white-space: pre-wrap; word-break: break-word; max-height: 180px; overflow: auto;
    background: #221f1f; border: 1px solid #333; border-radius: 6px; padding: 10px; font-size: 12px;
    color: #c9c4be; -webkit-app-region: no-drag; }
  .spinner { width: 28px; height: 28px; margin: 0 auto; border-radius: 50%;
    border: 3px solid #3a3636; border-top-color: #e8e6e3; animation: spin 0.9s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .actions { display: flex; gap: 8px; justify-content: center; -webkit-app-region: no-drag; }
  button { font: inherit; padding: 6px 14px; border-radius: 6px; border: 1px solid #444;
    background: #262323; color: #e8e6e3; cursor: pointer; }
  button.primary { background: #e8e6e3; color: #171616; border-color: #e8e6e3; }
</style></head>
<body><main data-phase="${phase}">
  ${copy.busy ? '<div class="spinner"></div>' : ''}
  <h1>${escapeHtml(copy.title)}</h1>
  ${copy.body ? `<p>${escapeHtml(copy.body)}</p>` : ''}
  ${error}
  ${buttons}
</main>
<script>
  const desktop = window.rdstDesktop && window.rdstDesktop.startup
  document.getElementById('logs')?.addEventListener('click', () => desktop?.openLogs())
  document.getElementById('retry')?.addEventListener('click', () => desktop?.retry())
  document.getElementById('reload')?.addEventListener('click', () => desktop?.reload())
</script>
</body></html>`
}

export function startupPageUrl(state: BackendStartState): string {
  return `data:text/html;charset=utf-8,${encodeURIComponent(startupPageHtml(state))}`
}
