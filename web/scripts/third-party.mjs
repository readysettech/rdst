/**
 * Third-party components a build ships, with the license texts each one carries.
 *
 * The web bundle records the npm packages and assets it contains in `third-party.json`
 * (the Vite plugin below). The desktop build reads that record, adds its own components
 * and renders all of them into the notices file it installs.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

export const RECORD_FILE = 'third-party.json'

const LICENSE_FILE = /^(licen[cs]e|copying|notice)([.-].*)?$/i

const MIT_TEXT = `MIT License

Copyright (c) {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
`

const COMMERCIAL_NOTE =
  'Used under a commercial license held by Readyset. It is not covered by the ' +
  'Apache License 2.0 and is not licensed for use outside RDST.'

function declaredLicense(pkg) {
  const license = pkg.license ?? pkg.licenses
  if (typeof license === 'string') return license
  if (Array.isArray(license)) return license.map((entry) => entry.type ?? entry).join(' OR ')
  return license?.type ?? 'UNKNOWN'
}

function authorName(author) {
  if (!author) return null
  if (typeof author === 'string') return author.replace(/\s*[<(].*$/, '').trim() || null
  return author.name ?? null
}

/** A package's name, version, declared license and the license texts it ships. */
export function packageComponent(dir) {
  const pkg = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'))
  const license = declaredLicense(pkg)
  const texts = readdirSync(dir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && LICENSE_FILE.test(entry.name))
    .map((entry) => entry.name)
    .sort()
    .map((file) => ({ file, text: readFileSync(join(dir, file), 'utf8') }))
  if (texts.length > 0) return { name: pkg.name, version: pkg.version, license, texts }
  if (license !== 'MIT') {
    throw new Error(`${pkg.name}@${pkg.version} ships no license file for its ${license} license`)
  }
  const holder = authorName(pkg.author) ?? `the ${pkg.name} authors`
  return {
    name: pkg.name,
    version: pkg.version,
    license,
    note: 'The package ships no license file; this is the standard text of its declared license.',
    texts: [{ file: null, text: MIT_TEXT.replace('{holder}', holder) }],
  }
}

/** The directory of the npm package a bundled module came from, or null for first-party code. */
export function packageDir(moduleId) {
  const file = moduleId.replace(/^\0/, '').split('?')[0].replaceAll('\\', '/')
  const at = file.lastIndexOf('/node_modules/')
  if (at < 0) return null
  const [first, second] = file.slice(at + '/node_modules/'.length).split('/')
  const name = first.startsWith('@') ? `${first}/${second}` : first
  return `${file.slice(0, at)}/node_modules/${name}`
}

function byName(a, b) {
  return a.name.localeCompare(b.name) || String(a.version).localeCompare(String(b.version))
}

/** Every npm package that contributed a module to the bundle. */
export function bundleComponents(moduleIds) {
  const dirs = new Set(moduleIds.map(packageDir).filter(Boolean))
  return [...dirs].map(packageComponent).sort(byName)
}

/**
 * The fonts and icon artwork the bundle ships. fonts/licenses.json declares each font family's
 * files and license, so every font file needs an entry there.
 */
export function assetComponents(publicDir, iconsDir) {
  const fontsDir = join(publicDir, 'fonts')
  const declared = existsSync(join(fontsDir, 'licenses.json'))
    ? JSON.parse(readFileSync(join(fontsDir, 'licenses.json'), 'utf8'))
    : {}
  const components = new Map()
  for (const file of existsSync(fontsDir) ? readdirSync(fontsDir) : []) {
    if (!/\.(ttf|otf|woff2?)$/i.test(file)) continue
    const entry = Object.entries(declared).find(([, font]) => font.files.includes(file))
    if (!entry) throw new Error(`fonts/licenses.json declares no license for ${file}`)
    const [name, font] = entry
    if (components.has(name)) continue
    const texts = font.text
      ? [{ file: font.text, text: readFileSync(join(fontsDir, font.text), 'utf8') }]
      : []
    components.set(name, { name, license: font.license, note: font.note, texts })
  }
  // Open builds ship the free Hugeicons set and say so in ATTRIBUTION.md; official builds
  // ship the commercial set.
  const attribution = join(iconsDir, 'svg', 'ATTRIBUTION.md')
  components.set(
    'Hugeicons',
    existsSync(attribution)
      ? {
          name: 'Hugeicons (free set)',
          license: 'MIT',
          texts: [{ file: 'ATTRIBUTION.md', text: readFileSync(attribution, 'utf8') }],
        }
      : { name: 'Hugeicons Pro', license: 'Commercial', note: COMMERCIAL_NOTE, texts: [] }
  )
  return [...components.values()].sort(byName)
}

// Packages whose code reaches the built CSS through @import rather than as a bundled module:
// Tailwind's base styles.
const CSS_PACKAGES = ['tailwindcss']

/** Vite plugin: emits the bundle's third-party record beside index.html. */
export function thirdPartyRecord() {
  let config
  return {
    name: 'rdst-third-party-record',
    apply: 'build',
    configResolved(resolved) {
      config = resolved
    },
    generateBundle(_options, bundle) {
      const moduleIds = Object.values(bundle).flatMap((output) =>
        output.type === 'chunk' ? Object.keys(output.modules) : []
      )
      const bundled = bundleComponents(moduleIds)
      const css = CSS_PACKAGES.filter((name) => !bundled.some((c) => c.name === name)).map(
        (name) => packageComponent(join(config.root, 'node_modules', name))
      )
      const components = [
        ...[...bundled, ...css].sort(byName),
        ...assetComponents(config.publicDir, join(config.root, 'node_modules/@rs/ui-icons')),
      ]
      this.emitFile({
        type: 'asset',
        fileName: RECORD_FILE,
        source: `${JSON.stringify({ components }, null, 1)}\n`,
      })
    },
  }
}
