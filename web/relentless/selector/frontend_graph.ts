import {
  existsSync,
  readdirSync,
  readFileSync,
  realpathSync,
  statSync,
} from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, extname, join, relative, resolve } from 'node:path'

// The detector step installs TypeScript on its own; NODE_PATH points at it.
const ts = createRequire(import.meta.url)(
  'typescript'
) as typeof import('typescript')

/**
 * A declaration-level dependency graph of TypeScript sources. A declaration is
 * a top-level function, class, variable, or export; `<module>` holds a file's
 * other top-level statements. Edges run from a declaration to what it uses at
 * runtime: type-only imports and references in type positions are left out,
 * so a shared file such as `lib/api.ts` connects each consumer only to the
 * functions it calls. String literals naming `/api/` paths are recorded as the
 * declaration's calls into the backend.
 */

export const MODULE = '<module>'
export const WHOLE = '*'

export type Node = `${string}#${string}`
export const node = (file: string, decl: string): Node => `${file}#${decl}`
export const fileOf = (n: Node) => n.slice(0, n.lastIndexOf('#'))
export const declOf = (n: Node) => n.slice(n.lastIndexOf('#') + 1)

type Span = [number, number]

interface FileInfo {
  /** decl -> line ranges; `<module>` can own many separate statements */
  spans: Map<string, Span[]>
  /** decl -> nodes it uses */
  refs: Map<string, Set<Node>>
  /** decl -> `/api/...` path templates it names */
  apiCalls: Map<string, Set<string>>
  /** Modules this file re-exports with `export *`. */
  starExports: string[]
}

export interface Graph {
  root: string
  files: Map<string, FileInfo>
  reverse: Map<Node, Set<Node>>
}

const SOURCE = /\.(tsx?|mts|cts)$/
const SKIP =
  /(\.test\.|\.spec\.|\/test-utils\.|\/__tests__\/|\/node_modules\/|\.d\.ts$)/

function sourceFiles(dir: string, out: string[] = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (entry === 'node_modules' || entry.startsWith('.')) continue
    if (statSync(path).isDirectory()) sourceFiles(path, out)
    else if (SOURCE.test(entry) && !SKIP.test(path)) out.push(path)
  }
  return out
}

/** `/api/x/${id}/y?${q}` becomes `/api/x/{}/y`. */
export function apiTemplate(text: string): string | null {
  const start = text.indexOf('/api/')
  if (start < 0) return null
  const path = text
    .slice(start)
    .split(/[?#\s'"`]/)[0]
    // A substitution glued to the end of a segment appends a query string.
    .replace(/([^/])\$\{[^}]*\}$/, '$1')
  const template = path.replace(/\$\{[^}]*\}|\{[^}]*\}/g, '{}')
  // `/api/` alone names the API as a whole, as a prefix test does, not a route.
  if (template === '/api/') return null
  // A path ending in `/` is completed by concatenation: match what follows.
  return template.endsWith('/') ? `${template}**` : template
}

const METHODS = new Set(['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])

/**
 * The HTTP method a path literal is requested with: `client.POST(path)`, or
 * `fetch(path, { method })`, where no method means GET. `*` when unknown.
 */
function callMethod(literal: import('typescript').Node): string {
  const call = literal.parent
  if (!call || !ts.isCallExpression(call) || call.arguments[0] !== literal) {
    return '*'
  }
  const callee = call.expression
  if (ts.isPropertyAccessExpression(callee) && METHODS.has(callee.name.text)) {
    return callee.name.text
  }
  if (ts.isIdentifier(callee) && callee.text === 'fetch') {
    const init = call.arguments[1]
    if (!init) return 'GET'
    if (ts.isObjectLiteralExpression(init)) {
      for (const property of init.properties) {
        if (
          ts.isPropertyAssignment(property) &&
          ts.isIdentifier(property.name) &&
          property.name.text === 'method'
        ) {
          return ts.isStringLiteral(property.initializer)
            ? property.initializer.text.toUpperCase()
            : '*'
        }
      }
      return 'GET'
    }
  }
  return '*'
}

/**
 * The text a template spells, with each substitution as `${}`. A substitution
 * naming a module constant, or calling a one-expression helper, spells that
 * constant's or helper's text, so `${base(id)}/health` keeps its `/api/` prefix.
 */
function templateText(
  node: import('typescript').TemplateExpression,
  folded: Map<string, string> = new Map()
) {
  const spell = (e: import('typescript').Expression) => {
    const name = ts.isIdentifier(e)
      ? e.text
      : ts.isCallExpression(e) && ts.isIdentifier(e.expression)
        ? e.expression.text
        : null
    return name ? folded.get(name) : undefined
  }
  return (
    node.head.text +
    node.templateSpans
      .map((s) => {
        const text = spell(s.expression)
        return text === undefined
          ? `\${}${s.literal.text}`
          : text + s.literal.text
      })
      .join('')
  )
}

/** Top-level `const X = '...'` and `const f = (...) => `...`` texts, by name. */
function foldedStrings(source: import('typescript').SourceFile) {
  const folded = new Map<string, string>()
  const textOf = (e: import('typescript').Expression): string | null => {
    if (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e))
      return e.text
    if (ts.isTemplateExpression(e)) return templateText(e, folded)
    if (ts.isArrowFunction(e) && !ts.isBlock(e.body)) return textOf(e.body)
    if (ts.isParenthesizedExpression(e)) return textOf(e.expression)
    return null
  }
  for (const statement of source.statements) {
    if (!ts.isVariableStatement(statement)) continue
    for (const d of statement.declarationList.declarations) {
      if (!ts.isIdentifier(d.name) || !d.initializer) continue
      const text = textOf(d.initializer)
      if (text !== null) folded.set(d.name.text, text)
    }
  }
  return folded
}

function isTypePosition(n: import('typescript').Node): boolean {
  for (let p = n.parent; p; p = p.parent) {
    // `extends Base` on a class is a runtime dependency; `implements` is not.
    if (ts.isHeritageClause(p)) {
      return (
        p.token === ts.SyntaxKind.ImplementsKeyword ||
        ts.isInterfaceDeclaration(p.parent)
      )
    }
    if (ts.isTypeNode(p) && !ts.isExpressionWithTypeArguments(p)) return true
    if (ts.isStatement(p) || ts.isSourceFile(p)) return false
  }
  return false
}

/** The binding names a top-level statement declares. */
function declaredNames(statement: import('typescript').Statement): string[] {
  const names: string[] = []
  const exported = ts
    .getModifiers(statement as import('typescript').HasModifiers)
    ?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)
  const isDefault = ts
    .getModifiers(statement as import('typescript').HasModifiers)
    ?.some((m) => m.kind === ts.SyntaxKind.DefaultKeyword)
  if (
    ts.isFunctionDeclaration(statement) ||
    ts.isClassDeclaration(statement) ||
    ts.isEnumDeclaration(statement)
  ) {
    if (statement.name) names.push(statement.name.text)
    if (exported && isDefault) names.push('default')
  } else if (ts.isVariableStatement(statement)) {
    const visit = (name: import('typescript').BindingName) => {
      if (ts.isIdentifier(name)) names.push(name.text)
      else
        for (const element of name.elements) {
          if (!ts.isOmittedExpression(element)) visit(element.name)
        }
    }
    for (const declaration of statement.declarationList.declarations) {
      visit(declaration.name)
    }
  } else if (ts.isExportAssignment(statement)) {
    names.push('default')
  }
  return names
}

interface WorkspacePackage {
  dir: string
  main?: string
  exports: Record<string, string>
}

// Workspace packages are read from their own package.json, so a checkout without pnpm's
// node_modules links resolves `@rs/ui-new/button` to the same file as one with them.
function workspacePackages(root: string): Map<string, WorkspacePackage> {
  const packages = new Map<string, WorkspacePackage>()
  const base = join(root, 'web-apps/packages')
  if (!existsSync(base)) return packages
  for (const entry of readdirSync(base)) {
    const manifest = join(base, entry, 'package.json')
    if (!existsSync(manifest)) continue
    const data = JSON.parse(readFileSync(manifest, 'utf8')) as {
      name?: string
      main?: string
      exports?: string | Record<string, unknown>
    }
    if (!data.name) continue
    const exports: Record<string, string> = {}
    if (typeof data.exports === 'string') exports['.'] = data.exports
    else
      for (const [key, value] of Object.entries(data.exports ?? {}))
        if (typeof value === 'string') exports[key] = value
    packages.set(data.name, {
      dir: join(base, entry),
      main: data.main,
      exports,
    })
  }
  return packages
}

function resolveWorkspaceImport(
  packages: Map<string, WorkspacePackage>,
  specifier: string,
  from: string,
  compilerOptions: import('typescript').CompilerOptions
): string | undefined {
  const [scope, name, ...rest] = specifier.split('/')
  const pkg = packages.get(`${scope}/${name}`)
  if (!pkg) return undefined
  const subpath = rest.length > 0 ? `./${rest.join('/')}` : '.'
  const target =
    pkg.exports[subpath] ?? (subpath === '.' ? pkg.main : undefined) ?? subpath
  const candidate = join(pkg.dir, target)
  if (existsSync(candidate) && statSync(candidate).isFile()) return candidate
  return ts.resolveModuleName(candidate, from, compilerOptions, ts.sys)
    .resolvedModule?.resolvedFileName
}

export function buildGraph(
  root: string,
  dirs: string[],
  compilerOptions: import('typescript').CompilerOptions
): Graph {
  const files = new Map<string, FileInfo>()
  const host = ts.sys
  const packages = workspacePackages(root)
  const resolveSpecifier = (specifier: string, from: string) => {
    const resolved = ts.resolveModuleName(
      specifier,
      from,
      compilerOptions,
      host
    ).resolvedModule?.resolvedFileName
    let path =
      resolved ??
      resolveWorkspaceImport(packages, specifier, from, compilerOptions)
    if (!path && specifier.startsWith('.')) {
      // Stylesheets and other assets TypeScript does not resolve.
      path = resolve(dirname(from), specifier)
    }
    if (!path) return null
    try {
      path = realpathSync(path)
    } catch {
      return null
    }
    if (path.includes('/node_modules/') || !path.startsWith(root)) return null
    return relative(root, path)
  }

  for (const dir of dirs) {
    // A configured source directory can be missing from an older tree.
    if (!existsSync(dir)) continue
    for (const absolute of sourceFiles(dir)) {
      const file = relative(root, absolute)
      const text = readFileSync(absolute, 'utf8')
      const source = ts.createSourceFile(
        absolute,
        text,
        ts.ScriptTarget.Latest,
        true,
        extname(absolute) === '.tsx' ? ts.ScriptKind.TSX : ts.ScriptKind.TS
      )
      const info: FileInfo = {
        spans: new Map(),
        refs: new Map(),
        apiCalls: new Map(),
        starExports: [],
      }
      files.set(file, info)
      // Names bound by imports, to the node they refer to.
      const imported = new Map<string, Node>()
      const local = new Set<string>()
      const folded = foldedStrings(source)
      const lineOf = (pos: number) =>
        source.getLineAndCharacterOfPosition(pos).line + 1
      const addSpan = (decl: string, s: import('typescript').Node) => {
        const span: Span = [lineOf(s.getStart(source)), lineOf(s.getEnd())]
        info.spans.set(decl, [...(info.spans.get(decl) ?? []), span])
      }
      const refsOf = (decl: string) => {
        let set = info.refs.get(decl)
        if (!set) {
          set = new Set()
          info.refs.set(decl, set)
        }
        return set
      }

      for (const statement of source.statements) {
        if (ts.isImportDeclaration(statement)) {
          const target = resolveSpecifier(
            (statement.moduleSpecifier as import('typescript').StringLiteral)
              .text,
            absolute
          )
          const clause = statement.importClause
          addSpan(MODULE, statement)
          if (!target) continue
          if (!clause) {
            refsOf(MODULE).add(node(target, WHOLE))
            continue
          }
          if (clause.isTypeOnly) continue
          if (clause.name)
            imported.set(clause.name.text, node(target, 'default'))
          const bindings = clause.namedBindings
          if (bindings && ts.isNamespaceImport(bindings)) {
            imported.set(bindings.name.text, node(target, WHOLE))
          } else if (bindings) {
            for (const element of bindings.elements) {
              if (element.isTypeOnly) continue
              imported.set(
                element.name.text,
                node(target, (element.propertyName ?? element.name).text)
              )
            }
          }
        } else if (ts.isExportDeclaration(statement)) {
          if (statement.isTypeOnly) continue
          const target = statement.moduleSpecifier
            ? resolveSpecifier(
                (
                  statement.moduleSpecifier as import('typescript').StringLiteral
                ).text,
                absolute
              )
            : null
          const clause = statement.exportClause
          if (!clause && target) info.starExports.push(target)
          else if (clause && ts.isNamedExports(clause)) {
            for (const element of clause.elements) {
              if (element.isTypeOnly) continue
              const name = element.name.text
              const from = (element.propertyName ?? element.name).text
              addSpan(name, statement)
              refsOf(name).add(target ? node(target, from) : node(file, from))
            }
          }
        } else {
          for (const name of declaredNames(statement)) local.add(name)
        }
      }

      for (const statement of source.statements) {
        if (
          ts.isImportDeclaration(statement) ||
          ts.isExportDeclaration(statement) ||
          ts.isInterfaceDeclaration(statement) ||
          ts.isTypeAliasDeclaration(statement)
        ) {
          continue
        }
        const decls = declaredNames(statement)
        const names = decls.length > 0 ? decls : [MODULE]
        const uses = new Set<Node>()
        const calls = new Set<string>()
        const visit = (n: import('typescript').Node) => {
          if (ts.isIdentifier(n) && !isTypePosition(n)) {
            const parent = n.parent
            const isPropertyName =
              (ts.isPropertyAccessExpression(parent) && parent.name === n) ||
              (ts.isPropertyAssignment(parent) && parent.name === n) ||
              (ts.isJsxAttribute(parent) && parent.name === n)
            if (!isPropertyName) {
              const target = imported.get(n.text)
              if (target) {
                // `ns.member` on a namespace import depends on that member only.
                if (
                  declOf(target) === WHOLE &&
                  ts.isPropertyAccessExpression(parent) &&
                  parent.expression === n
                ) {
                  uses.add(node(fileOf(target), parent.name.text))
                } else uses.add(target)
              } else if (local.has(n.text)) uses.add(node(file, n.text))
            }
          } else if (
            ts.isStringLiteral(n) ||
            ts.isNoSubstitutionTemplateLiteral(n)
          ) {
            const template = apiTemplate(n.text)
            if (template) calls.add(`${callMethod(n)} ${template}`)
          } else if (ts.isTemplateExpression(n)) {
            const template = apiTemplate(templateText(n, folded))
            if (template) calls.add(`${callMethod(n)} ${template}`)
          } else if (
            ts.isCallExpression(n) &&
            n.expression.kind === ts.SyntaxKind.ImportKeyword &&
            n.arguments[0] &&
            ts.isStringLiteral(n.arguments[0])
          ) {
            const target = resolveSpecifier(n.arguments[0].text, absolute)
            if (target) uses.add(node(target, WHOLE))
          }
          ts.forEachChild(n, visit)
        }
        visit(statement)
        for (const name of names) {
          addSpan(name, statement)
          const set = refsOf(name)
          for (const use of uses) if (use !== node(file, name)) set.add(use)
          if (calls.size > 0) {
            const existing = info.apiCalls.get(name) ?? new Set()
            for (const call of calls) existing.add(call)
            info.apiCalls.set(name, existing)
          }
        }
      }
      // Every declaration runs after the file's top-level statements.
      if (info.refs.has(MODULE) || info.spans.has(MODULE)) {
        for (const decl of info.spans.keys()) {
          if (decl !== MODULE) refsOf(decl).add(node(file, MODULE))
        }
      }
      // Using the whole module means using each declaration.
      const whole = refsOf(WHOLE)
      for (const decl of info.spans.keys()) whole.add(node(file, decl))
    }
  }

  // `export *` passes through names a file does not declare itself.
  const resolveStar = (target: Node, seen = new Set<string>()): Node[] => {
    const file = fileOf(target)
    const info = files.get(file)
    if (!info || seen.has(file)) return [target]
    const decl = declOf(target)
    if (decl === WHOLE || info.spans.has(decl)) return [target]
    seen.add(file)
    const found = info.starExports.flatMap((star) =>
      resolveStar(node(star, decl), seen)
    )
    return found.length > 0 ? found : [target]
  }

  const reverse = new Map<Node, Set<Node>>()
  for (const [file, info] of files) {
    for (const [decl, uses] of info.refs) {
      for (const use of uses) {
        for (const target of resolveStar(use)) {
          let set = reverse.get(target)
          if (!set) {
            set = new Set()
            reverse.set(target, set)
          }
          set.add(node(file, decl))
        }
      }
    }
  }
  return { root, files, reverse }
}

/** Declarations of `file` whose lines intersect the ranges, in `text` if given. */
export function declsIn(
  graph: Graph,
  file: string,
  ranges: [number, number][],
  text?: string
): Set<string> {
  const spans =
    text === undefined ? graph.files.get(file)?.spans : buildSingle(file, text)
  const touched = new Set<string>()
  if (!spans) return new Set([WHOLE])
  for (const [start, end] of ranges) {
    let hit = false
    for (const [decl, list] of spans) {
      if (list.some(([a, b]) => a <= end && start <= b)) {
        touched.add(decl)
        hit = true
      }
    }
    if (!hit) touched.add(MODULE)
  }
  return touched.has(MODULE) ? new Set([WHOLE]) : touched
}

/** Spans of a file's declarations, parsed from `text` (an older revision). */
function buildSingle(file: string, text: string) {
  const source = ts.createSourceFile(
    file,
    text,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  )
  const spans = new Map<string, Span[]>()
  const lineOf = (pos: number) =>
    source.getLineAndCharacterOfPosition(pos).line + 1
  for (const statement of source.statements) {
    const names = ts.isImportDeclaration(statement)
      ? [MODULE]
      : declaredNames(statement)
    const span: Span = [
      lineOf(statement.getStart(source)),
      lineOf(statement.getEnd()),
    ]
    for (const name of names.length > 0 ? names : [MODULE]) {
      spans.set(name, [...(spans.get(name) ?? []), span])
    }
  }
  return spans
}

/**
 * Walks reverse edges from the seeds. `stop(file)` marks files the walk
 * records but does not pass through: area entry points and app roots.
 */
export function propagate(
  graph: Graph,
  seeds: Map<Node, string[]>,
  stop: (file: string) => boolean
): Map<Node, string[]> {
  const reached = new Map(seeds)
  const queue = [...seeds.keys()]
  while (queue.length > 0) {
    const current = queue.shift() as Node
    if (stop(fileOf(current))) continue
    const consumers = new Set(graph.reverse.get(current) ?? [])
    if (declOf(current) !== WHOLE) {
      for (const c of graph.reverse.get(node(fileOf(current), WHOLE)) ?? []) {
        consumers.add(c)
      }
    }
    for (const consumer of consumers) {
      if (reached.has(consumer)) continue
      const chain = reached.get(current) ?? []
      const step =
        declOf(consumer) === WHOLE || declOf(consumer) === MODULE
          ? fileOf(consumer)
          : `${fileOf(consumer)}::${declOf(consumer)}`
      reached.set(consumer, [...chain, step])
      queue.push(consumer)
    }
  }
  return reached
}

/** Does a frontend call (`METHOD /api/...`) request a backend route? */
export function matchesRoute(call: string, method: string, route: string) {
  const space = call.indexOf(' ')
  const callMethod = call.slice(0, space)
  if (callMethod !== '*' && method !== 'API_ROUTE' && callMethod !== method) {
    return false
  }
  return matchesPath(call.slice(space + 1), route)
}

function matchesPath(call: string, route: string) {
  const prefix = call.endsWith('/**')
  const a = (prefix ? call.slice(0, -3) : call).split('/')
  const b = route
    .replace(/\{[^}]*\}/g, '{}')
    .replace(/\/$/, '')
    .split('/')
  if (prefix ? b.length <= a.length : a.length !== b.length) return false
  return a.every((part, i) => segmentMatches(part, b[i]))
}

/** `{}` stands for a whole segment or, like `${p}-status`, part of one. */
function segmentMatches(call: string, route: string) {
  if (call === route || call === '{}' || route === '{}') return true
  if (!call.includes('{}')) return false
  const pattern = call
    .split('{}')
    .map((piece) => piece.replace(/[.*+?^$()|[\]\\]/g, '\\$&'))
    .join('[^/]+')
  return new RegExp(`^${pattern}$`).test(route)
}
