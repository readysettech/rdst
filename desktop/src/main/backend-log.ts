import fs from 'node:fs'
import path from 'node:path'

const DEFAULT_MAX_BYTES = 5 * 1024 * 1024
const DEFAULT_KEEP = 3
export const MAX_LINE_BYTES = 64 * 1024

export type LogSource = 'stdout' | 'stderr' | 'shell'

/** Splits a byte stream into whole lines, holding back a trailing partial line. */
export class LineSplitter {
  private partial = ''
  private discarding = false

  push(chunk: string): string[] {
    const lines: string[] = []
    for (const part of chunk.replace(/\r/g, '').split(/(?<=\n)/)) {
      const complete = part.endsWith('\n')
      if (!this.discarding) {
        const available = MAX_LINE_BYTES - Buffer.byteLength(this.partial)
        const text = complete ? part.slice(0, -1) : part
        const bytes = Buffer.from(text.slice(0, available))
        this.partial += bytes.subarray(0, available).toString('utf8')
        if (Buffer.byteLength(this.partial) > MAX_LINE_BYTES) this.partial = this.partial.slice(0, -1)
        if (bytes.length > available || text.length > available) this.discarding = true
      }
      if (complete) {
        lines.push(this.partial)
        this.partial = ''
        this.discarding = false
      }
    }
    return lines
  }

  flush(): string[] {
    const rest = this.partial
    this.partial = ''
    this.discarding = false
    return rest ? [rest] : []
  }
}

/**
 * The backend's stdout and stderr, plus the shell's own notes about it, in one file the
 * person can open from the tray. When the file reaches `maxBytes` it moves to
 * `backend.1.log` (and older ones shift up), keeping `keep` old files.
 */
export class BackendLog {
  readonly file: string
  private fd: number | null = null
  private size = 0

  constructor(
    readonly dir: string,
    private readonly maxBytes = DEFAULT_MAX_BYTES,
    private readonly keep = DEFAULT_KEEP,
    name = 'backend.log'
  ) {
    this.file = path.join(dir, name)
  }

  private rotated(index: number): string {
    const ext = path.extname(this.file)
    const stem = ext ? this.file.slice(0, -ext.length) : this.file
    return `${stem}.${index}${ext}`
  }

  private open(): number {
    if (this.fd !== null) return this.fd
    fs.mkdirSync(this.dir, { recursive: true })
    try {
      this.size = fs.statSync(this.file).size
    } catch {
      this.size = 0
    }
    if (this.size >= this.maxBytes) this.rotate()
    this.fd = fs.openSync(this.file, 'a')
    return this.fd
  }

  private rotate(): void {
    if (this.fd !== null) {
      fs.closeSync(this.fd)
      this.fd = null
    }
    fs.rmSync(this.rotated(this.keep), { force: true })
    for (let index = this.keep - 1; index >= 1; index -= 1) {
      if (fs.existsSync(this.rotated(index))) fs.renameSync(this.rotated(index), this.rotated(index + 1))
    }
    if (fs.existsSync(this.file)) fs.renameSync(this.file, this.rotated(1))
    this.size = 0
  }

  /** Append whole lines; a failure to log never reaches the caller. */
  write(source: LogSource, lines: string[], now: Date = new Date()): void {
    if (lines.length === 0) return
    try {
      const stamp = now.toISOString()
      const text = lines.map((line) => `${stamp} [${source}] ${line}\n`).join('')
      const fd = this.open()
      fs.writeSync(fd, text)
      this.size += Buffer.byteLength(text)
      if (this.size >= this.maxBytes) this.rotate()
    } catch (error) {
      console.warn('[rdst-desktop] could not write the backend log:', error)
    }
  }

  close(): void {
    if (this.fd === null) return
    try {
      fs.closeSync(this.fd)
    } catch {
      // Already closed.
    }
    this.fd = null
  }
}
