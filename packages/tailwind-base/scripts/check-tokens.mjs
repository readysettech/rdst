#!/usr/bin/env node
/**
 * Design-system token guardrail (T18 / C-07).
 *
 * Grep of app class names against the @rs/tailwind-base token list, per the
 * design-system §9 guardrail: "undefined-token drift cannot reappear". The
 * highest-blast-radius CL of the launch stack is the token retarget; this check
 * keeps every later CL honest.
 *
 * Two failing checks:
 *   1. UNDEFINED TOKENS — every `bg-surface-*`, `text-content-*`,
 *      `border-border-*` (and ring/fill/stroke/from/to/via… variants) class
 *      must reference a token defined in style.css. Catches the exact drift the
 *      spec names (`surface-informative-soft`, `content-negative-1`), plus our
 *      new `shadow-glow-*` / `shadow-elevation-*` families.
 *   2. AD-HOC VALUES — arbitrary Tailwind bracket utilities carrying a raw
 *      color (`shadow-[…rgba…]`, `bg-[#…]`). New ones fail; a small, explicit
 *      allowlist carries the values that landed CLs deferred to a named later
 *      wave (each entry cites its migration ticket). Both the allowlist and
 *      the known-drift ledger are COUNT-PINNED: any growth past the recorded
 *      baseline fails (a 15th `bg-surface-layout-3` or a duplicated
 *      allowlisted glow cannot land silently).
 *   3. CONTRAST RISK — `text-content-layout-3` sharing a line with
 *      `bg-surface-raised`/`bg-surface-overlay` fails: tertiary grey is below
 *      AA-text on the raised tiers (4.22:1 on overlay) — AA-critical text
 *      there uses `content-layout-2` (T19+ rule, evidence/token-contrast-table.md).
 *   4. RAW PALETTE COLORS (P3-0/D4) — Tailwind's built-in palette
 *      (`emerald-400`, `bg-amber-500/10`, …) bypasses the semantic-token
 *      layer entirely, so checks 1–3 above can't see it. Every raw palette
 *      class fails unless it's pre-existing drift within its file's
 *      COUNT-PINNED `KNOWN_PALETTE` baseline (same ratchet as `KNOWN_UNDEFINED`:
 *      growth past the baseline fails, shrinkage prints a lower-the-pin nudge).
 *      Migrate these to semantic tokens (severity colors → `content-positive`/
 *      `content-warning`/`content-negative`, brand accents → the brand tokens)
 *      in P3-2/P3-3; this check only stops new ones from landing.
 *
 * Scope = the rdst dependency surface: apps/rdst/src + packages/ui-new/src.
 * (.qpdemo raw hex in demo.tsx is a CSS-variable block, not a class utility, and
 *  is migrated to content-viz-* in T22 — out of this className-scoped grep.)
 *
 * Run: `node packages/tailwind-base/scripts/check-tokens.mjs` (wired into
 * `turbo lint` via tailwind-base's `lint` script). Exit 1 on any violation.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const TW_BASE = join(HERE, '..') // packages/tailwind-base
const WORKSPACE = join(TW_BASE, '..', '..') // web-apps
const STYLE_CSS = join(TW_BASE, 'style.css')

const SCAN_DIRS = [
  join(WORKSPACE, 'apps', 'rdst', 'src'),
  join(WORKSPACE, 'packages', 'ui-new', 'src'),
]

// Ad-hoc bracket values that landed CLs intentionally deferred to a later wave.
// Keyed by file suffix + exact utility so a line move never breaks the check;
// each MUST cite the wave that removes it. New arbitrary values are NOT welcome.
// Pre-existing undefined-token drift the §4.6 audit under-counted (present at
// HEAD before C-07). These rendered no color and lived in screen/component
// files migrated across the launch stack. COUNT-PINNED: the check FAILS if a
// name's count exceeds its baseline (no silent growth).
//   surface-layout-3 → surface-raised / surface-layout-disabled  (C-09/T21–T22)
//   border-negative  → border-border-negative-soft               (C-09/T21–T23)
//   border-warning   → border-border-warning-soft                (C-09/T21–T23)
// DRIVEN TO ZERO in C-09 (T21–T23 screen migrations): all three families now
// consume real tokens, so the ledger is empty. The map stays (empty) so the
// ratchet is documented and any re-introduction of these undefined tokens
// fails as new drift, not as a pinned baseline.
const KNOWN_UNDEFINED = new Map([])

// COUNT-PINNED like KNOWN_UNDEFINED: `count` is the exact number of matches
// allowed for that value in that file — a duplicated allowlisted value fails.
//
// The AnalysisSections verdict-card glows (4×) folded into the shadow-glow-*
// tokens in C-09 (T21 analyze migration) and left the allowlist. The remaining
// app-chrome shadows were removed in the Queries closeout, so new arbitrary
// shadows now fail without an exception.
const ALLOWLIST = []

// Raw Tailwind PALETTE color classes (P3-0/D4 audit, 2026-07-22): pre-existing
// usages that bypass the semantic-token layer, keyed by file with the exact
// count at HEAD. COUNT-PINNED like KNOWN_UNDEFINED: a file's count may never
// grow past its baseline (a new `text-emerald-400` fails immediately, whether
// the file already has drift or not — files absent from this map have a
// baseline of 0). Driven to 0 across ReportDialog/SchemaEmptyState/SchemaAdd*/
// -demo-page in P3-2/P3-3 as each migrates to semantic tokens.
// Emptied 2026-07-22: raw palette usage was driven to zero in the P3-2
// migrations, so every raw palette class is now a hard failure.
const KNOWN_PALETTE = new Map([])

// ---- 1. Parse the valid token names out of style.css -----------------------
const css = readFileSync(STYLE_CSS, 'utf8')
const collect = (prefix) => {
  const set = new Set()
  // e.g. `--color-surface-raised:` → `surface-raised`. Skip the `--x--sub`
  // metadata forms (line-height/font-weight) by rejecting a `-` right after.
  const re = new RegExp(`--${prefix}-([a-z0-9-]+?)\\s*:`, 'g')
  let m
  while ((m = re.exec(css))) {
    const name = m[1]
    if (name.includes('--')) continue
    set.add(name)
  }
  return set
}
const COLOR = collect('color') // surface-*, content-*, border-*, mono, …
const SHADOW = collect('shadow') // elevation-1, glow-negative, small, focus, …

// ---- 2. Walk the scan dirs -------------------------------------------------
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
    } else if (/\.(tsx?|css)$/.test(e) && !/\.test\.(tsx?)$/.test(e)) {
      out.push(p)
    }
  }
  return out
}
const files = SCAN_DIRS.flatMap((d) => walk(d))

// Utility-class family prefixes that can carry a color / shadow token.
const FAMILY = String.raw`(?:bg|text|border|border-[xytblr]|ring|ring-offset|fill|stroke|from|to|via|divide|outline|decoration|caret|accent|placeholder|shadow)`
// A class token: optional variant prefixes, family, then the token name.
const CLASS_RE = new RegExp(
  String.raw`(?<![\w-])(?:[a-z][a-z0-9-]*:|(?:group|peer)-[a-z]+:|data-\[[^\]]*\]:|aria-\[[^\]]*\]:)*(` +
    FAMILY +
    String.raw`)-([a-z][a-z0-9./-]*|\[[^\]]*\])`,
  'g'
)

// Strip `//` line comments and `/* */` block comments while preserving line
// count, so class-like text in doc comments (e.g. this file's own
// `shadow-glow-{…}` prose) is not scanned. Guards `://` in URLs.
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
      if (line[i] === '/' && line[i + 1] === '/' && line[i - 1] !== ':') break
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

const undefinedTokens = [] // {file, line, cls} — NEW drift, fails
const knownDrift = [] // {file, line, cls} — pre-existing, count-pinned
const adhocValues = [] // {file, line, cls}
const contrastRisk = [] // {file, line} — content-layout-3 on raised/overlay
const paletteHits = [] // {file, line, cls} — raw palette color, count-pinned per file
for (const a of ALLOWLIST) a.measured = 0

// T19+ AA rule (evidence/token-contrast-table.md): content-layout-3 is only
// 4.22:1 on surface-overlay / 4.50:1 on surface-raised — AA-critical text on
// the raised tiers must use content-layout-2. Same-line co-occurrence is the
// static approximation (one element's className); split-line recipes are the
// review checklist's job.
const RAISED_RE = /(?<![\w-])bg-surface-(?:raised|overlay)(?![\w-])/
const TERTIARY_RE = /(?<![\w-])text-content-layout-3(?![\w-])/

// Raw Tailwind palette color classes — the built-in scale, not our semantic
// tokens. Matches e.g. `text-emerald-400`, `bg-amber-500/10`, `from-violet-500`.
// Reuses FAMILY so every color-carrying utility prefix is covered.
const PALETTE_RE = new RegExp(
  String.raw`\b` +
    FAMILY +
    String.raw`-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}(?:\/\d+)?\b`,
  'g'
)

for (const file of files) {
  const rel = relative(WORKSPACE, file)
  const lines = stripComments(readFileSync(file, 'utf8').split('\n'))
  lines.forEach((line, i) => {
    let m
    CLASS_RE.lastIndex = 0
    while ((m = CLASS_RE.exec(line))) {
      const family = m[1]
      let name = m[2]
      const full = `${family}-${name}`

      // Arbitrary bracket value carrying a raw color? Named CSS colors count
      // too — bg-[black]/50 slipped the hex/rgba-only pattern (C-08 fix; the
      // scrim backdrops now use the surface-scrim token).
      if (name.startsWith('[')) {
        if (
          !/#[0-9a-fA-F]{3,8}|rgba?\(|(?<![\w-])(?:black|white)(?![\w-])/.test(
            name
          )
        )
          continue // e.g. shadow-[inset_…] w/o color, w-[12px]
        const entry = ALLOWLIST.find(
          (a) => file.endsWith(a.file) && a.value === full
        )
        if (entry) entry.measured += 1
        else adhocValues.push({ file: rel, line: i + 1, cls: full })
        continue
      }

      name = name.replace(/\/\d+$/, '') // strip opacity modifier

      // Undefined semantic-color token? (surface-*, content-*, border-* families)
      const isColorFamily =
        name.startsWith('surface-') ||
        name.startsWith('content-') ||
        ((family === 'border' || /^border-[xytblr]$/.test(family)) &&
          name.startsWith('border-'))
      if (isColorFamily && !COLOR.has(name)) {
        ;(KNOWN_UNDEFINED.has(name) ? knownDrift : undefinedTokens).push({
          file: rel,
          line: i + 1,
          cls: full,
          name,
        })
        continue
      }
      // Undefined shadow token in our semantic families?
      if (
        family === 'shadow' &&
        (name.startsWith('elevation-') || name.startsWith('glow-')) &&
        !SHADOW.has(name)
      ) {
        ;(KNOWN_UNDEFINED.has(name) ? knownDrift : undefinedTokens).push({
          file: rel,
          line: i + 1,
          cls: full,
          name,
        })
      }
    }
    // T19+ AA rule: tertiary text may not share an element with a raised tier.
    if (RAISED_RE.test(line) && TERTIARY_RE.test(line)) {
      contrastRisk.push({ file: rel, line: i + 1 })
    }
    // Raw Tailwind palette color — bypasses the semantic-token layer.
    let p
    PALETTE_RE.lastIndex = 0
    while ((p = PALETTE_RE.exec(line))) {
      paletteHits.push({ file: rel, line: i + 1, cls: p[0] })
    }
  })
}

// ---- 3. Report -------------------------------------------------------------
let failed = false
if (undefinedTokens.length) {
  failed = true
  console.error(
    `\n✗ ${undefinedTokens.length} undefined-token class(es) — not in @rs/tailwind-base:`
  )
  for (const v of undefinedTokens)
    console.error(`  ${v.file}:${v.line}  ${v.cls}`)
}
if (adhocValues.length) {
  failed = true
  console.error(
    `\n✗ ${adhocValues.length} ad-hoc arbitrary color value(s) — use a token or add to the allowlist with a migration ticket:`
  )
  for (const v of adhocValues) console.error(`  ${v.file}:${v.line}  ${v.cls}`)
}
if (contrastRisk.length) {
  failed = true
  console.error(
    `\n✗ ${contrastRisk.length} contrast-risk element(s) — text-content-layout-3 on bg-surface-raised/overlay is below AA-text (4.22:1 on overlay); use text-content-layout-2 (see evidence/token-contrast-table.md):`
  )
  for (const v of contrastRisk) console.error(`  ${v.file}:${v.line}`)
}

// Count-pin: raw palette usage may never grow past its per-file baseline
// (files with no baseline entry have an implicit baseline of 0, so any hit in
// a fresh file fails immediately — this is what stops NEW palette usage).
const paletteByFile = new Map()
for (const v of paletteHits)
  paletteByFile.set(v.file, (paletteByFile.get(v.file) || 0) + 1)
const paletteLower = []
for (const [file, measured] of paletteByFile) {
  const baseline = KNOWN_PALETTE.get(file) || 0
  if (measured > baseline) {
    failed = true
    console.error(
      `\n✗ raw palette color(s) grew in ${file}: ${measured} usages > pinned baseline ${baseline}. Use a semantic token instead:`
    )
    for (const v of paletteHits.filter((h) => h.file === file))
      console.error(`  ${v.file}:${v.line}  ${v.cls}`)
  }
}
for (const [file, baseline] of KNOWN_PALETTE) {
  const measured = paletteByFile.get(file) || 0
  if (measured < baseline) paletteLower.push(`${file} ${measured}<${baseline}`)
}

// Count-pin: known drift may never grow past its baseline…
const driftByName = new Map()
for (const v of knownDrift)
  driftByName.set(v.name, (driftByName.get(v.name) || 0) + 1)
const pinLower = []
for (const [name, baseline] of KNOWN_UNDEFINED) {
  const measured = driftByName.get(name) || 0
  if (measured > baseline) {
    failed = true
    console.error(
      `\n✗ known-drift token "${name}" grew: ${measured} usages > pinned baseline ${baseline}. New usages of an undefined token are not allowed — use a real token.`
    )
  } else if (measured < baseline) {
    pinLower.push(`${name} ${measured}<${baseline}`)
  }
}
// …and an allowlisted ad-hoc value may never be duplicated.
const allowLower = []
for (const a of ALLOWLIST) {
  if (a.measured > a.count) {
    failed = true
    console.error(
      `\n✗ allowlisted ad-hoc value duplicated: "${a.value}" in ${a.file} — ${a.measured} matches > pinned ${a.count}. Use the shadow-glow-*/elevation-* token instead.`
    )
  } else if (a.measured < a.count) {
    allowLower.push(`${a.file} ${a.value} ${a.measured}<${a.count}`)
  }
}

if (failed) {
  console.error(
    '\nDesign-system token check FAILED. See docs/launch-audit/redesign/design-system.md §9.\n'
  )
  process.exit(1)
}
console.log(
  `✓ token check passed — ${files.length} files, ${COLOR.size} color + ${SHADOW.size} shadow tokens, 0 new undefined, 0 unlisted ad-hoc values, 0 contrast risks, ${paletteHits.length} raw palette usage(s) within pinned baselines.`
)
if (knownDrift.length) {
  const byToken = {}
  for (const v of knownDrift) byToken[v.cls] = (byToken[v.cls] || 0) + 1
  console.log(
    `  note: ${knownDrift.length} pre-existing known-drift usage(s) within pinned baselines, pending screen migration: ` +
      Object.entries(byToken)
        .map(([k, n]) => `${k}×${n}`)
        .join(', ')
  )
}
for (const s of pinLower)
  console.log(`  note: drift shrank — lower the KNOWN_UNDEFINED pin: ${s}`)
for (const s of allowLower)
  console.log(
    `  note: allowlist entry now unused/partial — remove or lower it: ${s}`
  )
for (const s of paletteLower)
  console.log(`  note: palette usage shrank — lower the KNOWN_PALETTE pin: ${s}`)
