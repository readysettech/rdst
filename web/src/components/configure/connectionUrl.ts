// Connection strings for PostgreSQL and MySQL, read into the fields a database needs.

export interface ParsedConnectionUrl {
  engine: string
  host: string
  port: number
  database: string
  user: string
  password: string
  tls: boolean
  tlsVerify: boolean
  tlsCa: string
}

export const defaultPortFor = (engine: string) =>
  engine === 'mysql' ? 3306 : 5432

export function parseConnectionUrl(url: string): ParsedConnectionUrl | null {
  try {
    // Handle postgres:// as alias for postgresql://
    const normalizedUrl = url.replace(/^postgres:\/\//, 'postgresql://')

    // Check for supported protocols
    if (
      !normalizedUrl.startsWith('postgresql://') &&
      !normalizedUrl.startsWith('mysql://')
    ) {
      return null
    }

    // Parse using URL API (replace protocol for parsing)
    const parsableUrl = normalizedUrl.replace(
      /^(postgresql|mysql):\/\//,
      'http://'
    )
    const parsed = new URL(parsableUrl)

    // Determine engine from original protocol
    const engine = normalizedUrl.startsWith('mysql://') ? 'mysql' : 'postgresql'
    const defaultPort = defaultPortFor(engine)

    // Extract database from pathname (remove leading slash)
    const database = parsed.pathname.replace(/^\//, '')
    const params = parsed.searchParams

    // Infer TLS/SSL from connection string query params
    let tls = false
    let tlsVerify = false
    let tlsCa = ''
    if (engine === 'postgresql') {
      const sslMode = (params.get('sslmode') || '').toLowerCase()
      tls =
        sslMode === 'require' ||
        sslMode === 'verify-ca' ||
        sslMode === 'verify-full'
      tlsVerify = sslMode === 'verify-ca' || sslMode === 'verify-full'
      tlsCa = params.get('sslrootcert') || ''
    } else {
      const ssl = (params.get('ssl') || '').toLowerCase()
      const sslMode = (params.get('ssl-mode') || '').toUpperCase()
      tls =
        ssl === 'true' ||
        ssl === '1' ||
        sslMode === 'REQUIRED' ||
        sslMode === 'VERIFY_CA' ||
        sslMode === 'VERIFY_IDENTITY'
      tlsVerify = sslMode === 'VERIFY_CA' || sslMode === 'VERIFY_IDENTITY'
      tlsCa = params.get('ssl-ca') || ''
    }

    return {
      engine,
      host: parsed.hostname || 'localhost',
      port: parsed.port ? Number.parseInt(parsed.port, 10) : defaultPort,
      database,
      user: parsed.username ? decodeURIComponent(parsed.username) : '',
      password: parsed.password ? decodeURIComponent(parsed.password) : '',
      tls,
      tlsVerify,
      tlsCa,
    }
  } catch {
    return null
  }
}
