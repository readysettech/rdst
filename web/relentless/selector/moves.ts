import { createRequire } from 'node:module'

const ts = createRequire(import.meta.url)(
  'typescript'
) as typeof import('typescript')

/** What else a change moved, by module name. */
export interface Moves {
  /** Old module name to new, for modules the change renamed. */
  renamed?: ReadonlyMap<string, string>
  /** Modules the change moved away from their old path. */
  moved?: ReadonlySet<string>
}

/**
 * Whether a changed file only moved: its code is the same before and after,
 * and each import names the same bindings from the same module, whose path
 * changed only because the change moved that module or this file. Fixing an
 * import of a module nothing moved is a change. A file whose path is
 * behavior, such as a route, cannot move without changing.
 */
export function onlyMoved(
  oldPath: string,
  newPath: string,
  oldSource: string,
  newSource: string,
  moves: Moves = {}
) {
  if (oldPath !== newPath && (ROUTE.test(oldPath) || ROUTE.test(newPath)))
    return false
  if (oldSource === newSource) return true
  const read = CODE_TS.test(newPath)
    ? typescriptParts
    : newPath.endsWith('.py')
      ? pythonParts
      : undefined
  if (!read) return false
  const before = read(oldPath, oldSource, moves.renamed)
  const after = read(newPath, newSource)
  if (
    before.body !== after.body ||
    before.imports.join('\n') !== after.imports.join('\n')
  )
    return false
  for (const [module, { names, paths }] of before.modules) {
    const now = after.modules.get(module)?.paths ?? []
    if (paths.join('\n') === now.join('\n')) continue
    const relative = [...paths, ...now].every((p) => p.startsWith('.'))
    if (oldPath !== newPath && relative) continue
    if (![...names].every((n) => moves.moved?.has(n))) return false
  }
  return true
}

// File-based routes: moving one changes its URL.
const ROUTE = /^web-apps\/apps\/rdst\/src\/routes\//
const CODE_TS = /\.[cm]?[jt]sx?$/

/** The last segment of a module path or specifier, without extension. */
export const moduleName = (specifier: string) =>
  specifier
    .replace(/\.(py|[cm]?[jt]sx?)$/, '')
    .replace(/\/(index|__init__)$/, '')
    .split(/[/.]/)
    .filter(Boolean)
    .at(-1) ?? ''

type Renamed = ReadonlyMap<string, string>

/** Per imported module: the names it was imported under, and its paths. */
class Modules extends Map<string, { names: Set<string>; paths: string[] }> {
  add(module: string, name: string, path: string) {
    const entry = this.get(module) ?? { names: new Set(), paths: [] }
    entry.names.add(name)
    entry.paths.push(path)
    entry.paths.sort()
    this.set(module, entry)
  }
}

function typescriptParts(path: string, source: string, renamed?: Renamed) {
  const file = ts.createSourceFile(
    path,
    source,
    ts.ScriptTarget.Latest,
    false,
    path.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
  )
  const imports: string[] = []
  const modules = new Modules()
  const body: string[] = []
  for (const s of file.statements) {
    const specifier =
      (ts.isImportDeclaration(s) || ts.isExportDeclaration(s)) &&
      s.moduleSpecifier &&
      ts.isStringLiteral(s.moduleSpecifier)
        ? s.moduleSpecifier.text
        : undefined
    if (specifier === undefined) {
      body.push(s.getText(file).replace(/\s+/g, ' '))
      continue
    }
    const name = moduleName(specifier)
    const module = renamed?.get(name) ?? name
    modules.add(module, name, specifier)
    if (ts.isImportDeclaration(s)) {
      const clause = s.importClause
      if (!clause) imports.push(`${module}:`)
      if (clause?.name) imports.push(`${module}:default:${clause.name.text}`)
      const bindings = clause?.namedBindings
      if (bindings && ts.isNamespaceImport(bindings))
        imports.push(`${module}:*:${bindings.name.text}`)
      if (bindings && ts.isNamedImports(bindings))
        for (const e of bindings.elements)
          imports.push(
            `${module}:${(e.propertyName ?? e.name).text}:${e.name.text}`
          )
    } else if (ts.isExportDeclaration(s)) {
      const clause = s.exportClause
      if (!clause) imports.push(`export ${module}:*`)
      else if (ts.isNamedExports(clause))
        for (const e of clause.elements)
          imports.push(
            `export ${module}:${(e.propertyName ?? e.name).getText(file)}:${e.name.getText(file)}`
          )
      else imports.push(`export ${module}:*:${clause.name.getText(file)}`)
    }
  }
  return { imports: imports.sort(), modules, body: body.join('\n') }
}

function pythonParts(_path: string, source: string, renamed?: Renamed) {
  const imports: string[] = []
  const modules = new Modules()
  const body: string[] = []
  const lines = source.split('\n')
  for (let i = 0; i < lines.length; i++) {
    let line = lines[i]
    if (!/^\s*(from\s+\S+\s+import\b|import\s)/.test(line)) {
      if (line.trim()) body.push(line.trimEnd())
      continue
    }
    // A statement continues over parentheses or a trailing backslash.
    while (
      i + 1 < lines.length &&
      ((line.includes('(') && !line.includes(')')) || line.endsWith('\\'))
    )
      line = `${line.replace(/\\$/, '')} ${lines[++i]}`
    const statement = line.replace(/#.*$/, '').replace(/[()]/g, ' ').trim()
    const from = statement.match(/^from\s+(\S+)\s+import\s+(.+)$/)
    const names = (list: string) =>
      list
        .split(',')
        .map((n) => n.trim().split(/\s+as\s+/))
        .filter(([n]) => n)
    if (from) {
      const name = moduleName(from[1])
      const module = renamed?.get(name) ?? name
      modules.add(module, name, from[1])
      for (const [binding, alias] of names(from[2]))
        imports.push(`${module}:${binding}:${alias ?? binding}`)
    } else {
      for (const [path, alias] of names(statement.replace(/^import\s+/, ''))) {
        const name = moduleName(path)
        const module = renamed?.get(name) ?? name
        modules.add(module, name, path)
        imports.push(`${module}:${alias ?? path}`)
      }
    }
  }
  return { imports: imports.sort(), modules, body: body.join('\n') }
}
