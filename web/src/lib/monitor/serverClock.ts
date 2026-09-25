// The backend's clock as seen from here. Reading times come from the backend, so a
// browser whose clock runs ahead or behind (a WSL backend drifts from Windows) would
// fill every "next reading" ring early or late. Each monitor response's Date header
// says the backend's time to the second; offsets under two seconds are noise.

const NOISE_S = 2
const SAMPLES = 5

let samples: number[] = []
let offset = 0

/** Records one response's Date header against the local clock. */
export function noteServerDate(header: string | null, localMs = Date.now()) {
  if (!header) return
  const serverMs = Date.parse(header)
  if (Number.isNaN(serverMs)) return
  // The header truncates to the second, so the true time is up to a second later.
  samples = [...samples, (serverMs + 500 - localMs) / 1000].slice(-SAMPLES)
  const sorted = [...samples].sort((a, b) => a - b)
  const median = sorted[Math.floor(sorted.length / 2)]
  offset = Math.abs(median) < NOISE_S ? 0 : median
}

/** Seconds to add to the local clock to get the backend's. */
export function serverOffset(): number {
  return offset
}

/** Tests: forget what was measured. */
export function resetServerClock() {
  samples = []
  offset = 0
}
