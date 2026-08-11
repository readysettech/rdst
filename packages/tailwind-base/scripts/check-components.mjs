#!/usr/bin/env node
/**
 * Raw-`<button>` guardrail (P3-0/D4).
 *
 * `@rs/ui-new` owns every button primitive used by RDST: `Button` for labeled
 * actions, `IconButton` for icon-only actions, `InteractiveRow` for dense
 * rows, and style-neutral `Pressable` for bespoke controls such as scrims,
 * option cards, and OS chrome. A raw `<button>` at an app call site bypasses
 * those shared semantics and focus treatments, so the allowed baseline is 0.
 *
 * Scope = apps/rdst/src only (packages/ui-new is where `Button` itself is
 * implemented, so it necessarily contains the real `<button>` element and is
 * out of scope for this check).
 *
 * Run: `node packages/tailwind-base/scripts/check-components.mjs`, or via
 * `turbo lint` (this package's lint script chains it after check-tokens).
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const TW_BASE = join(HERE, '..') // packages/tailwind-base
const WORKSPACE = join(TW_BASE, '..', '..') // web-apps
const SCAN_DIR = join(WORKSPACE, 'apps', 'rdst', 'src')

const KNOWN_BUTTONS = new Map()

function walk(dir, out = []) {
  let entries
  try {
    entries = readdirSync(dir)
  } catch {
    return out
  }
  for (const e of entries) {
    const p = join(dir, e)
    const s = statSync(p)
    if (s.isDirectory()) {
      if (e === 'node_modules' || e === 'dist' || e === '.output') continue
      walk(p, out)
    } else if (/\.tsx?$/.test(e) && !/\.test\.tsx?$/.test(e)) {
      out.push(p)
    }
  }
  return out
}
const files = walk(SCAN_DIR)

// Strip `//` line comments and `/* */` block comments while preserving line
// count, so a mention of `<button` in prose (like this file's own header)
// never counts as a hit.
function stripComments(lines) {
  let inBlock = false
  return lines.map((line) => {
    let out = ''
    let i = 0
    while (i < line.length) {
      if (inBlock) {
        const end = line.indexOf('*/', i)
        if (end === -1) {
          i = line.length
        } else {
          i = end + 2
          inBlock = false
        }
        continue
      }
      if (line[i] === '/' && line[i + 1] === '/') break
      if (line[i] === '/' && line[i + 1] === '*') {
        inBlock = true
        i += 2
        continue
      }
      out += line[i]
      i++
    }
    return out
  })
}

const BUTTON_RE = /<button\b/g

const hits = [] // {file, line}
for (const file of files) {
  const rel = relative(WORKSPACE, file)
  const lines = stripComments(readFileSync(file, 'utf8').split('\n'))
  lines.forEach((line, i) => {
    BUTTON_RE.lastIndex = 0
    while (BUTTON_RE.exec(line)) hits.push({ file: rel, line: i + 1 })
  })
}

const byFile = new Map()
for (const h of hits) byFile.set(h.file, (byFile.get(h.file) || 0) + 1)

let failed = false
const grown = []
for (const [file, measured] of byFile) {
  const baseline = KNOWN_BUTTONS.get(file) || 0
  if (measured > baseline) {
    failed = true
    grown.push({ file, measured, baseline })
  }
}

if (failed) {
  console.error(
    `\n✗ raw <button> count grew past its pinned baseline in ${grown.length} file(s):`
  )
  for (const g of grown.sort((a, b) => b.measured - a.measured))
    console.error(`  ${g.file}: ${g.measured} usages > pinned baseline ${g.baseline}`)
  console.error(
    '\nUse @rs/ui-new Button, IconButton, InteractiveRow, or Pressable instead of a raw <button>.\n'
  )
  process.exit(1)
}

const total = hits.length
console.log(
  `✓ component check passed — ${files.length} files scanned, ${total} raw <button> usage(s).`
)
const lower = []
for (const [file, baseline] of KNOWN_BUTTONS) {
  const measured = byFile.get(file) || 0
  if (measured < baseline) lower.push(`${file} ${measured}<${baseline}`)
}
for (const s of lower)
  console.log(`  note: usage shrank — lower (or remove) the KNOWN_BUTTONS pin: ${s}`)
