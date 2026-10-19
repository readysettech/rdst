import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { isProductionKeyservice } from '../driver/mail.ts'
import {
  changeBrief,
  changePacket,
  filterDiff,
  stackPosition,
} from './change.ts'
import { classifyPackageJson, classifyPyproject } from './classify.ts'
import { buildGraph, matchesRoute } from './frontend_graph.ts'
import { templateFor } from './ingest-evidence.ts'
import { askModel } from './model.ts'
import { onlyMoved } from './moves.ts'
import {
  type Change,
  checkContract,
  contract,
  globRegex,
  graphOwners,
  nearestOwners,
  parseDiff,
  repoRoot,
  routeTable,
  select,
} from './selector.ts'

// Run with `node --test relentless/selector/selector-tests.ts`. The cases
// change one line of real files, so they follow the code as it moves.

function lineOf(file: string, needle: string) {
  const line =
    readFileSync(join(repoRoot, file), 'utf8')
      .split('\n')
      .findIndex((l) => l.includes(needle)) + 1
  assert.ok(line > 0, `${needle} is not in ${file}`)
  return line
}

function edit(path: string, needle: string): Change {
  const line = lineOf(path, needle)
  return {
    path,
    oldPath: path,
    status: 'modified',
    newRanges: [[line, line]],
    oldRanges: [[line, line]],
  }
}

function touch(path: string): Change {
  return {
    path,
    oldPath: path,
    status: 'modified',
    newRanges: [[1, 1]],
    oldRanges: [[1, 1]],
  }
}

function trackedFiles() {
  const tracked = existsSync(join(repoRoot, '.git'))
    ? execFileSync('git', ['ls-files'], {
        cwd: repoRoot,
        encoding: 'utf8',
        maxBuffer: 64 << 20,
      })
    : execFileSync('jj', ['file', 'list'], {
        cwd: repoRoot,
        encoding: 'utf8',
        maxBuffer: 64 << 20,
      })
  return tracked.split('\n').filter(Boolean)
}

test('the contract matches the areas, routes, and rules', () => {
  assert.deepEqual(checkContract(trackedFiles()), [])
})

test('a new file outside every slice needs no manifest entry', () => {
  const brandNew = 'web-apps/apps/rdst/src/brand-new/thing.ts'
  assert.deepEqual(checkContract([...trackedFiles(), brandNew]), [])
  // Nothing imports it yet, so no page can show it.
  const selection = select([{ ...touch(brandNew), status: 'added' }])
  assert.equal(selection.status, 'none')
  assert.deepEqual(selection.ignored, [
    { file: brandNew, reason: 'no page or API route reaches it' },
  ])
})

test('unowned code takes the areas of the owners nearest to it in the graphs', () => {
  const names = (path: string) =>
    [
      ...(graphOwners([touch(path)])
        .get(path)
        ?.keys() ?? []),
    ]
      .map((c) => c.name)
      .sort()
  assert.deepEqual(names('web-apps/apps/rdst/src/lib/targetQueries.ts'), [
    'connections',
    'settings',
  ])
  assert.deepEqual(names('web-apps/apps/rdst/src/lib/useDemo.ts'), ['platform'])

  // An owner reached only through another owner's code is not nearest.
  const origin = 'web-apps/apps/rdst/src/lib/helper.ts'
  const fleet = 'web-apps/apps/rdst/src/components/configure/useFleet.ts'
  const ask = 'web-apps/apps/rdst/src/features/ask/AskPanel.tsx'
  const nearest = nearestOwners(
    origin,
    new Map([
      [fleet, [origin, `${fleet}::useFleet`]],
      [ask, [origin, `${fleet}::useFleet`, `${ask}::AskPanel`]],
    ])
  )
  assert.deepEqual(
    [...nearest].map(([c, use]) => [c.name, use.via, use.declarations]),
    [['connections', fleet, 1]]
  )
})

test('code no capability owns follows its users in the graphs', () => {
  const s = select([
    edit('web-apps/apps/rdst/src/lib/useDemo.ts', 'export type Phase'),
  ])
  assert.equal(s.status, 'targeted')
  assert.deepEqual(s.recommended_areas, ['platform'])
  // Only the Readyset Platform page uses it, so it is that page's.
  assert.deepEqual(s.owners, ['platform'])
  // Paths only older trees had are declared for replays alone.
  assert.deepEqual(
    select([touch('rdst/lib/api/docker_prepull.py')]).recommended_areas,
    []
  )
})

test('shared infrastructure is broad and runs the smoke set', () => {
  // The HTTP client every page requests through, and the app's entry point.
  for (const change of [
    edit('web-apps/apps/rdst/src/lib/client.ts', 'export'),
    edit('web-apps/apps/rdst/src/main.tsx', 'import'),
  ]) {
    const s = select([change])
    assert.equal(s.status, 'broad')
    assert.deepEqual(s.recommended_areas, contract.smoke)
  }
  // One request helper in a shared module reaches only its callers.
  const s = select([
    edit(
      'web-apps/apps/rdst/src/lib/api.ts',
      'export async function fetchAskHistory('
    ),
  ])
  assert.equal(s.status, 'targeted')
  assert.ok(!s.recommended_areas.includes('shell-navigation'))
})

test('backend code runs the areas of the capability owning it', () => {
  const s = select([
    edit('rdst/features/ask/api/routes.py', 'def _phase_value'),
  ])
  assert.deepEqual(s.recommended_areas, ['ask'])
  assert.deepEqual(s.owners, ['ask'])
})

test('a path built from a module constant or URL helper names its route', () => {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'rt-graph-')))
  try {
    writeFileSync(
      join(root, 'api.ts'),
      `const ROOT = '/api/monitor'
const base = (t: string) => \`\${ROOT}/targets/\${t}\`
const query = (q: object) => \`?\${new URLSearchParams(String(q))}\`
export const health = (t: string) => fetch(\`\${base(t)}/health\${query({})}\`)
export const limit = () => fetch(\`\${ROOT}/limit\`, { method: 'POST' })
`
    )
    const calls = buildGraph(root, [root], {}).files.get('api.ts')?.apiCalls
    const health = [...(calls?.get('health') ?? [])]
    const limit = [...(calls?.get('limit') ?? [])]
    assert.ok(
      health.some((c) =>
        matchesRoute(c, 'GET', '/api/monitor/targets/{target}/health')
      ),
      health.join(',')
    )
    assert.ok(
      limit.some((c) => matchesRoute(c, 'POST', '/api/monitor/limit')),
      limit.join(',')
    )
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

test('monitoring code runs the areas that watch it', () => {
  const engine = select([edit('rdst/features/monitor/loop.py', 'def ')])
  assert.deepEqual(engine.owners.sort(), ['inbox', 'monitoring'])
  const page = select([
    edit('web-apps/apps/rdst/src/features/database/DatabasePage.tsx', 'export'),
  ])
  assert.ok(page.owners.includes('monitoring'), JSON.stringify(page.reasons))
})

test('a folder naming only its capability takes the areas named once', () => {
  // rdst/features/configure says only {"capability": "connections"}.
  const s = select([edit('rdst/features/configure/service.py', 'def ')])
  assert.deepEqual(s.owners.sort(), ['onboarding-connect', 'settings-targets'])
})

test('the graphs suggest flows a change reaches without editing', () => {
  // run_audit starts a background run through the frontend's run client.
  const s = select([edit('rdst/features/audit/api/routes.py', 'def run_audit')])
  assert.deepEqual(s.flows, [])
  const suggested = s.suggested_flows.find(
    (f) => f.id === 'activity/server-run-reload'
  )
  assert.ok(suggested, JSON.stringify(s.suggested_flows))
  assert.match(suggested.reasons[0], /POST \/api\/audit -> /)
})

test('the API app runs app lifecycle and the start-up flows', () => {
  const s = select([
    edit('rdst/shared/api/app.py', 'include_router(account.router'),
  ])
  assert.deepEqual(s.recommended_areas, ['app-lifecycle'])
  assert.deepEqual(
    s.flows.map((f) => f.id),
    ['server/browser-start', 'server/desktop-sidecar-start']
  )
})

test('a widely used design-system component is broad, not all areas', () => {
  const s = select([
    edit(
      'web-apps/packages/ui-new/src/components/element/button.tsx',
      'export'
    ),
  ])
  assert.equal(s.status, 'broad')
  assert.equal(s.recommended_areas.length, contract.budget.areas)
})

test('code outside the app selects nothing', () => {
  assert.equal(select([edit('rdst/rdst.py', 'import')]).status, 'none')
  assert.equal(select([touch('aidoc/README.md')]).status, 'none')
  assert.equal(
    select([touch('rdst/tests/unit/test_ask_schema_context.py')]).status,
    'none'
  )
})

test('desktop and tester changes pick their own areas', () => {
  assert.deepEqual(
    select([
      edit('web-apps/apps/rdst-desktop/src/main/backend.ts', 'export'),
    ]).recommended_areas.sort(),
    ['app-lifecycle', 'desktop-shell']
  )
  assert.deepEqual(
    select([touch('web-apps/apps/rdst/relentless/areas/inbox.md')])
      .recommended_areas,
    ['inbox']
  )
  assert.deepEqual(
    select([touch('web-apps/apps/rdst/relentless/driver/session.ts')])
      .recommended_areas,
    ['ai-access']
  )
})

test('a file that only moved is the same code under new import paths', () => {
  const panel = `import { useFleet } from '../lib/useFleet'
import type { Target } from '../lib/targets'

export function Panel() {
  return useFleet().length
}
`
  const moved = panel
    .replace("'../lib/useFleet'", "'@/features/connections/useFleet'")
    .replace("'../lib/targets'", "'../connections/targets'")
  const fleetMoved = { moved: new Set(['useFleet', 'targets']) }
  const [from, to] = ['src/components/Panel.tsx', 'src/features/Panel.tsx']
  assert.ok(onlyMoved(from, to, panel, panel))
  assert.ok(onlyMoved(from, to, panel, moved, fleetMoved))
  assert.ok(onlyMoved('a.tsx', 'a.tsx', panel, moved, fleetMoved))
  // A new path to a module nothing moved fixes or swaps an import.
  assert.ok(!onlyMoved('a.tsx', 'a.tsx', panel, moved))
  // The file's own move changes its relative paths, but not its aliased ones.
  const relative = panel.replaceAll('../lib/', '../../lib/')
  assert.ok(onlyMoved(from, to, panel, relative))
  assert.ok(!onlyMoved('a.tsx', 'a.tsx', panel, relative))
  // Another module, even with the same bindings, is a change.
  const mock = panel.replace("useFleet'", "mockFleet'")
  assert.ok(!onlyMoved('a.tsx', 'a.tsx', panel, mock, fleetMoved))
  // Unless the change renamed that module.
  const renamed = {
    renamed: new Map([['useFleet', 'fleetStore']]),
    moved: new Set(['useFleet']),
  }
  const store = panel.replace("'../lib/useFleet'", "'../jobs/fleetStore'")
  assert.ok(onlyMoved('a.tsx', 'a.tsx', panel, store, renamed))
  const edited = moved.replace('.length', '.length + 1')
  assert.ok(!onlyMoved('a.tsx', 'a.tsx', panel, edited, fleetMoved))
  const aliased = moved.replace('{ useFleet }', '{ useFleet as f }')
  assert.ok(!onlyMoved('a.tsx', 'a.tsx', panel, aliased, fleetMoved))
  // A route's path is its URL; one edited in place can still only move imports.
  const route = 'web-apps/apps/rdst/src/routes/fleet.tsx'
  assert.ok(
    !onlyMoved(route, 'web-apps/apps/rdst/src/routes/targets.tsx', panel, panel)
  )
  assert.ok(onlyMoved(route, route, panel, moved, fleetMoved))

  const server = `from rdst.web.state import (
    registry,
    settings as web_settings,
)
import rdst.web.log as log

def run():
    return registry, web_settings, log
`
  const movedServer = server
    .replace('rdst.web.state', 'shared.api.state')
    .replace('rdst.web.log', 'shared.api.log')
  const webMoved = { moved: new Set(['state', 'log']) }
  const [oldServer, newServer] = [
    'rdst/web/server.py',
    'rdst/features/web/server.py',
  ]
  assert.ok(onlyMoved(oldServer, newServer, server, movedServer, webMoved))
  // Pointing an import at a module that exists fixes it.
  assert.ok(!onlyMoved('s.py', 's.py', server, movedServer))
  const rebound = movedServer.replace('registry,\n', 'registry as r,\n')
  assert.ok(!onlyMoved('s.py', 's.py', server, rebound, webMoved))
  const changed = movedServer.replace('return', 'return None,')
  assert.ok(!onlyMoved('s.py', 's.py', server, changed, webMoved))
})

test('moves select nothing, and instructions edited across areas run one', () => {
  const moved = {
    ...touch('web-apps/apps/rdst/src/features/connections/useFleet.ts'),
    moved: true,
  }
  const selection = select([moved])
  assert.deepEqual(selection.recommended_areas, [])
  assert.equal(selection.status, 'none')
  const areas = ['inbox', 'ask', 'app-lifecycle', 'monitoring']
  assert.deepEqual(
    select(
      areas.map((a) => touch(`web-apps/apps/rdst/relentless/areas/${a}.md`))
    ).recommended_areas,
    ['app-lifecycle']
  )
})

test('stored data carries a change to the areas that read it', () => {
  const s = select([edit('rdst/features/query_registry/service.py', 'def ')])
  assert.ok(s.affected_areas.includes('monitoring'))
  assert.ok(s.affected_areas.includes('ask'))
})

test('build configuration is broad and unknown app files are unknown', () => {
  assert.equal(select([touch('web-apps/pnpm-lock.yaml')]).status, 'broad')
  const s = select([touch('web-apps/apps/rdst/public/new-asset.bin')])
  assert.equal(s.status, 'unknown')
  assert.deepEqual(s.recommended_areas, contract.smoke)
})

test('diffs, globs, and route matching', () => {
  const [change] = parseDiff(
    'diff --git a/x.ts b/y.ts\nrename from x.ts\nrename to y.ts\n@@ -3,2 +3,0 @@\n@@ -10 +8,3 @@\n'
  )
  assert.equal(change.status, 'renamed')
  assert.equal(change.oldPath, 'x.ts')
  assert.deepEqual(change.oldRanges, [
    [3, 4],
    [10, 10],
  ])
  assert.deepEqual(change.newRanges, [
    [3, 3],
    [8, 10],
  ])
  assert.ok(globRegex('rdst/**/*.{md,txt}').test('rdst/a/b/c.md'))
  assert.ok(!globRegex('rdst/*.py').test('rdst/a/b.py'))
  assert.ok(
    matchesRoute(
      'GET /api/query-registry/{}',
      'GET',
      '/api/query-registry/{query_hash}'
    )
  )
  assert.ok(
    !matchesRoute(
      'DELETE /api/query-registry/{}',
      'POST',
      '/api/query-registry/load-test-runs'
    )
  )
  assert.ok(matchesRoute('* /api/runs/**', 'GET', '/api/runs/{run_id}/events'))
  assert.ok(
    matchesRoute(
      'GET /api/providers/{}-status',
      'GET',
      '/api/providers/aws-status'
    )
  )
  assert.ok(
    !matchesRoute(
      'GET /api/providers/{}-status',
      'GET',
      '/api/providers/aws-login'
    )
  )
})

test('overrides come due for review', () => {
  const tracked = existsSync(join(repoRoot, '.git'))
    ? execFileSync('git', ['ls-files'], {
        cwd: repoRoot,
        encoding: 'utf8',
        maxBuffer: 64 << 20,
      })
    : execFileSync('jj', ['file', 'list'], {
        cwd: repoRoot,
        encoding: 'utf8',
        maxBuffer: 64 << 20,
      })
  const later = checkContract(
    tracked.split('\n').filter(Boolean),
    new Date('2099-01-01')
  )
  assert.ok(
    later.some((p) => p.includes('was due for review')),
    later.join('\n')
  )
})

test('evidence maps a request to the most literal route', () => {
  const table = routeTable()
  assert.equal(
    templateFor('POST', '/api/query-registry/load-test-runs', table),
    'POST /api/query-registry/load-test-runs'
  )
  assert.equal(
    templateFor('GET', '/api/query-registry/abc123/analysis/latest?x=1', table),
    'GET /api/query-registry/{query_hash}/analysis/latest'
  )
  assert.equal(templateFor('GET', '/api/no-such-route', table), null)
})

test('manifests are read: runtime dependencies are broad, tooling and metadata are not', () => {
  const pkg = (
    deps: Record<string, string>,
    dev: Record<string, string>,
    extra = {}
  ) =>
    JSON.stringify({
      name: 'x',
      version: '1.0.0',
      dependencies: deps,
      devDependencies: dev,
      ...extra,
    })
  const base = pkg({ react: '19' }, { vitest: '3' })
  assert.equal(
    classifyPackageJson(base, pkg({ react: '19' }, { vitest: '4' })).runtime,
    false
  )
  assert.equal(
    classifyPackageJson(base, pkg({ react: '20' }, { vitest: '3' })).runtime,
    true
  )
  assert.equal(
    classifyPackageJson(base, pkg({ react: '19' }, { vitest: '3', vite: '8' }))
      .runtime,
    true
  )
  assert.equal(
    classifyPackageJson(
      base,
      pkg({ react: '19' }, { vitest: '3' }, { license: 'Apache-2.0' })
    ).runtime,
    false
  )
  const toml = (version: string, dep: string) =>
    `[project]\nname = "rdst"\nversion = "${version}"\ndependencies = [\n  "${dep}",\n]\n\n[tool.ruff]\nline-length = 100\n`
  assert.equal(
    classifyPyproject(
      toml('1', 'fastapi'),
      toml('2', 'fastapi'),
      [[3, 3]],
      [[3, 3]]
    ).runtime,
    false
  )
  assert.equal(
    classifyPyproject(
      toml('1', 'fastapi'),
      toml('1', 'fastapi>=1'),
      [[5, 5]],
      [[5, 5]]
    ).runtime,
    true
  )
  assert.equal(
    classifyPyproject(
      toml('1', 'fastapi'),
      toml('1', 'fastapi'),
      [[10, 10]],
      [[10, 10]]
    ).runtime,
    false
  )
})

test('trivial files select nothing and assets follow the code that loads them', () => {
  assert.equal(select([touch('rdst/LICENSE')]).status, 'none')
  assert.equal(select([touch('rdst/.gitignore')]).status, 'none')
  const template = select([
    touch('rdst/shared/deploy/templates/deploy_systemd.sh'),
  ])
  assert.ok(
    template.ignored.some((i) => i.reason.includes('script_generator.py'))
  )
  assert.notEqual(template.status, 'unknown')
})

test('code that starts the API server selects app lifecycle', () => {
  const s = select([edit('rdst/shared/api/app.py', 'def create_app')])
  assert.ok(
    s.affected_areas.includes('app-lifecycle'),
    JSON.stringify(s.reasons)
  )
  assert.match(s.reasons['app-lifecycle'][0], /the API app every page talks to/)
})

test('changes trigger the flows that cover them, within the mission budget', () => {
  const ids = (changes: Change[]) => select(changes).flows.map((f) => f.id)
  assert.ok(
    ids([
      edit('web-apps/apps/rdst/src/lib/backgroundRuns.ts', 'export'),
    ]).includes('activity/server-run-reload')
  )
  assert.ok(
    ids([edit('rdst/shared/run_registry.py', 'def ')]).includes(
      'activity/server-run-reload'
    )
  )
  assert.ok(
    ids([edit('rdst/features/monitor/connection.py', 'def ')]).includes(
      'monitoring/outage-alert'
    )
  )
  const start = ids([edit('rdst/shared/api/app.py', 'def create_app')])
  assert.ok(start.includes('server/browser-start'), start.join(','))
  assert.ok(start.includes('server/desktop-sidecar-start'), start.join(','))
  assert.deepEqual(ids([touch('aidoc/README.md')]), [])
  const s = select([edit('rdst/shared/api/app.py', 'def create_app')])
  assert.ok(s.mission_minutes <= (contract.budget.mission_minutes ?? 45))
})

test('tests and package metadata trigger no flow', () => {
  const ids = (changes: Change[]) => select(changes).flows.map((f) => f.id)
  assert.deepEqual(
    ids([
      touch(
        'web-apps/apps/rdst/src/features/queries/analyze-drawer/AnalyzeDrawer.test.tsx'
      ),
    ]),
    []
  )
  const manifest = 'web-apps/apps/rdst-desktop/package.json'
  const current = readFileSync(join(repoRoot, manifest), 'utf8')
  const described = {
    ...edit(manifest, '"description"'),
    oldSource: current.replace('"RDST desktop application"', '"RDST desktop"'),
  }
  assert.deepEqual(ids([described]), [])
})

test('a malformed model key is refused without being repeated', async () => {
  const key = 'sk-or-FAKE\nsecond-line'
  await assert.rejects(askModel('change', { key }), (error: Error) => {
    assert.ok(!String(error).includes('sk-or-FAKE'), String(error))
    return true
  })
})

test('only the tip of a stack is its tip, and the stack leaves from main', () => {
  // main <- a <- b <- c, open at their current patchsets; d sat on an
  // outdated patchset of b; m is merged history below a.
  const change = (
    n: number,
    commit: string,
    parent: string,
    status = 'NEW',
    current = true
  ) => ({
    _change_number: n,
    status,
    _revision_number: current ? 2 : 1,
    _current_revision_number: 2,
    commit: { commit, parents: [{ commit: parent }] },
  })
  const related = [
    change(4, 'd', 'b-old', 'NEW'),
    change(3, 'c', 'b'),
    change(2, 'b', 'a'),
    change(1, 'a', 'm'),
    change(0, 'm', 'main', 'MERGED'),
  ]
  assert.deepEqual(stackPosition(related, 'c'), {
    tip: true,
    above: [],
    base: 'm',
  })
  assert.deepEqual(stackPosition(related, 'b'), {
    tip: false,
    above: [3],
    base: 'm',
  })
  assert.deepEqual(stackPosition(related, 'a'), {
    tip: false,
    above: [3, 2],
    base: 'm',
  })
  assert.deepEqual(stackPosition([], 'x'), { tip: true, above: [], base: null })
})

test('a change packet holds every commit of a stack and its whole diff', () => {
  const root = mkdtempSync(join(tmpdir(), 'rt-change-'))
  try {
    const git = (...args: string[]) =>
      execFileSync(
        'git',
        ['-c', 'user.name=t', '-c', 'user.email=t@t', ...args],
        {
          cwd: root,
          encoding: 'utf8',
        }
      )
    git('init', '-q')
    writeFileSync(join(root, 'a.ts'), 'export const a = 1\n')
    git('add', '.')
    git('commit', '-qm', 'base')
    const base = git('rev-parse', 'HEAD').trim()
    writeFileSync(join(root, 'a.ts'), 'export const a = 2\n')
    git('commit', '-qam', 'First in the stack\n\nWhy it changes a.')
    writeFileSync(join(root, 'b.ts'), 'export const b = 1\n')
    writeFileSync(join(root, 'pnpm-lock.yaml'), 'lock\n')
    git('add', '.')
    git('commit', '-qm', 'Second in the stack')
    const packet = changePacket(`${base}..HEAD`, root)
    assert.deepEqual(
      packet.commits.map((c) => c.description.split('\n')[0]),
      ['First in the stack', 'Second in the stack']
    )
    assert.equal(packet.files.length, 3)
    assert.match(packet.diff, /export const a = 2/)
    assert.match(packet.diff, /export const b = 1/)
    assert.doesNotMatch(packet.diff, /pnpm-lock/)
    assert.match(changeBrief(packet), /a stack of 2 commits/)
    // A single revision is the range from its parent.
    assert.equal(changePacket('HEAD', root).commits.length, 1)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
  assert.equal(
    filterDiff(
      'diff --git a/x.svg b/x.svg\n+<svg/>\ndiff --git a/y.ts b/y.ts\n+y\n'
    ),
    'diff --git a/y.ts b/y.ts\n+y\n'
  )
})

test('account flows refuse every production keyservice host', () => {
  for (const url of [
    'https://keyservice.readyset.io',
    'https://KEYSERVICE.readyset.io/',
    'https://rdst-keyservice.readysetio.workers.dev/v1',
    'not a url',
  ])
    assert.ok(isProductionKeyservice(url), url)
  assert.ok(!isProductionKeyservice('https://keyservice-staging.readyset.io'))
})
