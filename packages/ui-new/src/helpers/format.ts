import { format, formatDistanceToNow, isValid, parseISO } from 'date-fns'

type InputDate = string | number | Date

const normalizeEpochToMs = (value: number): number => {
  const abs = Math.trunc(Math.abs(value))
  if (abs === 0) return 0
  const len = String(abs).length
  if (len <= 10) return value * 1000 // seconds -> ms
  if (len <= 13) return value // already ms
  if (len <= 16) return Math.floor(value / 1000) // microseconds -> ms
  return Math.floor(value / 1_000_000) // nanoseconds -> ms (best effort)
}

const coerceToDate = (input: InputDate): Date => {
  if (input instanceof Date) return input
  if (typeof input === 'number' && !Number.isNaN(input)) {
    return new Date(normalizeEpochToMs(input))
  }
  if (typeof input === 'string') {
    let s = input.trim()
    if (!s) return new Date(Number.NaN)
    const lower = s.toLowerCase()
    if (lower === 'nan' || lower === 'unknown' || lower === '--')
      return new Date(Number.NaN)
    if (/^\d+$/.test(s)) {
      const num = Number(s)
      return new Date(normalizeEpochToMs(num))
    }
    // Normalize common non-ISO formats Safari rejects
    // 1) "YYYY-MM-DD HH:mm:ss" -> "YYYY-MM-DDTHH:mm:ss"
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(s)) {
      s = s.replace(' ', 'T')
    }
    // 2) Handle timezone names at the end like " UTC" or " GMT"
    //    Replace with ISO Z (UTC)
    s = s.replace(/\s?(UTC|GMT)$/i, 'Z')
    // 3) Normalize numeric timezone without colon: +0000 -> +00:00
    s = s.replace(/([+-]\d{2})(\d{2})$/, '$1:$2')
    // If no timezone info, assume UTC to avoid Safari local parsing pitfalls
    const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(s)
    const normalized = hasTz ? s : `${s}Z`
    return parseISO(normalized)
  }
  return new Date(Number.NaN)
}

export const formatDateToRelativeTime = (value: InputDate): string => {
  const date = coerceToDate(value)
  if (!isValid(date)) return '--'
  return formatDistanceToNow(date, { addSuffix: true })
}

export const formatDate = (
  value: InputDate,
  formatStr = 'MMMM dd, yyyy, HH:mm'
): string => {
  const date = coerceToDate(value)
  if (!isValid(date)) return '--'
  return format(date, formatStr)
}

export const formatMoney = (amount: number) =>
  amount.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
  })
