import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

import { BackendLog, LineSplitter, MAX_LINE_BYTES } from './backend-log.js'

const temporary: string[] = []
afterEach(() => {
  for (const dir of temporary.splice(0)) fs.rmSync(dir, { recursive: true, force: true })
})

function tempDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rdst-log-test-'))
  temporary.push(dir)
  return dir
}

describe('LineSplitter', () => {
  it('caps complete and partial lines by bytes and resumes at the next newline', () => {
    const splitter = new LineSplitter()
    expect(splitter.push('x'.repeat(MAX_LINE_BYTES * 3))).toEqual([])
    expect(splitter.push('more')).toEqual([])
    const lines = splitter.push('\nnext\n' + 'y'.repeat(MAX_LINE_BYTES * 2) + '\n')
    expect(lines.map((line) => Buffer.byteLength(line))).toEqual([MAX_LINE_BYTES, 4, MAX_LINE_BYTES])
    expect(splitter.flush()).toEqual([])
    splitter.push('\u{1f642}'.repeat(MAX_LINE_BYTES))
    expect(Buffer.byteLength(splitter.flush()[0]!)).toBeLessThanOrEqual(MAX_LINE_BYTES)
  })
  it('holds a partial line until the rest arrives', () => {
    const splitter = new LineSplitter()
    expect(splitter.push('RDST_STARTUP {"sta')).toEqual([])
    expect(splitter.push('te":"migrating"}\r\nnext')).toEqual(['RDST_STARTUP {"state":"migrating"}'])
    expect(splitter.flush()).toEqual(['next'])
  })
})

describe('BackendLog', () => {
  it('stamps lines with their source and rotates at the size limit', () => {
    const dir = path.join(tempDir(), 'logs')
    const log = new BackendLog(dir, 200, 2)
    const at = new Date('2026-09-30T12:00:00Z')
    log.write('stdout', ['Starting RDST web server'], at)
    expect(fs.readFileSync(path.join(dir, 'backend.log'), 'utf8'))
      .toBe('2026-09-30T12:00:00.000Z [stdout] Starting RDST web server\n')
    for (let index = 0; index < 20; index += 1) log.write('stderr', [`line ${index}`], at)
    log.close()

    const files = fs.readdirSync(dir).sort()
    expect(files).toEqual(['backend.1.log', 'backend.2.log', 'backend.log'])
    for (const file of files) expect(fs.statSync(path.join(dir, file)).size).toBeLessThan(260)
    expect(fs.readFileSync(path.join(dir, 'backend.log'), 'utf8')).toContain('line 19')
  })

  it('rotates a file left over from a previous run that is already full', () => {
    const dir = tempDir()
    fs.writeFileSync(path.join(dir, 'backend.log'), 'x'.repeat(300))
    const log = new BackendLog(dir, 200, 3)
    log.write('shell', ['starting backend'])
    log.close()
    expect(fs.readFileSync(path.join(dir, 'backend.1.log'), 'utf8')).toBe('x'.repeat(300))
    expect(fs.readFileSync(path.join(dir, 'backend.log'), 'utf8')).toContain('[shell] starting backend')
  })
})
