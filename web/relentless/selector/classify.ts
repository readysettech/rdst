/**
 * Reads a changed dependency manifest to tell whether the change can alter
 * the shipped app: a runtime dependency, an export, a build tool, or
 * packaging, as opposed to metadata or tooling used only by tests and lint.
 */

export interface ManifestChange {
  runtime: boolean
  /** The keys or sections that changed, for the explanation. */
  changed: string[]
}

// Tools that never reach the built app. A build tool such as Vite does, so it
// is deliberately absent.
const TEST_TOOL =
  /^(vitest|@vitest\/|jsdom|happy-dom|@testing-library\/|@playwright\/|playwright|@biomejs\/|eslint|prettier|@types\/|msw$|@faker-js\/)/
const PACKAGE_RUNTIME_KEYS = new Set([
  'dependencies',
  'peerDependencies',
  'optionalDependencies',
  'bundleDependencies',
  'bundledDependencies',
  'main',
  'module',
  'exports',
  'imports',
  'type',
  'browser',
  'engines',
  'overrides',
  'pnpm',
  'workspaces',
  'build',
])
const TEST_SCRIPT = /^(test|lint|check|format|typecheck|e2e)([:.-]|$)/

function parseJson(text: string | undefined): Record<string, unknown> {
  try {
    return text ? (JSON.parse(text) as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

/** Which of two records' keys differ. */
function changedKeys(a: Record<string, unknown>, b: Record<string, unknown>) {
  return [...new Set([...Object.keys(a), ...Object.keys(b)])].filter(
    (key) => !same(a[key], b[key])
  )
}

export function classifyPackageJson(
  oldText: string | undefined,
  newText: string | undefined
): ManifestChange {
  const before = parseJson(oldText)
  const after = parseJson(newText)
  const changed: string[] = []
  let runtime = false
  for (const key of changedKeys(before, after)) {
    const a = (before[key] ?? {}) as Record<string, unknown>
    const b = (after[key] ?? {}) as Record<string, unknown>
    if (key === 'devDependencies') {
      const tools = changedKeys(a, b)
      changed.push(...tools.map((t) => `devDependencies.${t}`))
      if (tools.some((t) => !TEST_TOOL.test(t))) runtime = true
    } else if (key === 'scripts') {
      const scripts = changedKeys(a, b)
      changed.push(...scripts.map((t) => `scripts.${t}`))
      if (scripts.some((t) => !TEST_SCRIPT.test(t))) runtime = true
    } else {
      changed.push(key)
      if (PACKAGE_RUNTIME_KEYS.has(key)) runtime = true
    }
  }
  return { runtime, changed }
}

// `[project]` keys and table prefixes that reach the installed package.
const PYPROJECT_RUNTIME_KEYS = new Set([
  'dependencies',
  'optional-dependencies',
  'requires-python',
  'scripts',
  'gui-scripts',
  'entry-points',
])
const PYPROJECT_RUNTIME_TABLE =
  /^(build-system|project\.(optional-dependencies|scripts|gui-scripts|entry-points)|tool\.(setuptools|hatch\.build|poetry\.dependencies|uv\.sources))/

/** For each line, the TOML table and top-level key it belongs to. */
function tomlContext(text: string) {
  const context: { table: string; key: string }[] = []
  let table = ''
  let key = ''
  for (const line of text.split('\n')) {
    const header = line.match(/^\s*\[\[?([^\]]+)\]\]?/)
    if (header) {
      table = header[1].trim()
      key = ''
    } else {
      const assign = line.match(/^([A-Za-z0-9_.-]+)\s*=/)
      if (assign) key = assign[1]
    }
    context.push({ table, key })
  }
  return context
}

export function classifyPyproject(
  oldText: string | undefined,
  newText: string | undefined,
  oldRanges: [number, number][],
  newRanges: [number, number][]
): ManifestChange {
  const changed = new Set<string>()
  let runtime = false
  const visit = (text: string | undefined, ranges: [number, number][]) => {
    if (!text) return
    const context = tomlContext(text)
    for (const [start, end] of ranges) {
      for (let line = start; line <= end; line++) {
        const at = context[line - 1]
        if (!at) continue
        const name = at.key ? `${at.table}.${at.key}` : at.table
        changed.add(name)
        if (PYPROJECT_RUNTIME_TABLE.test(at.table)) runtime = true
        if (at.table === 'project' && PYPROJECT_RUNTIME_KEYS.has(at.key))
          runtime = true
      }
    }
  }
  visit(oldText, oldRanges)
  visit(newText, newRanges)
  return { runtime, changed: [...changed] }
}
