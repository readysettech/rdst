import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import { expect, it } from 'vitest'
import { databaseHref } from './snapshotModel'

// Midnight epochs calculated with Python zoneinfo, independently of JavaScript Date.
const cases = [
  ['America/Denver', '2026-03-08', 1772953200, 1773036000],
  ['America/Denver', '2026-11-01', 1793512800, 1793602800],
  ['America/Denver', '2026-03-29', 1774764000, 1774850400],
  ['America/Denver', '2026-10-25', 1792908000, 1792994400],
  ['America/New_York', '2026-03-08', 1772946000, 1773028800],
  ['America/New_York', '2026-11-01', 1793505600, 1793595600],
  ['America/New_York', '2026-03-29', 1774756800, 1774843200],
  ['America/New_York', '2026-10-25', 1792900800, 1792987200],
  ['Europe/London', '2026-03-08', 1772928000, 1773014400],
  ['Europe/London', '2026-11-01', 1793491200, 1793577600],
  ['Europe/London', '2026-03-29', 1774742400, 1774825200],
  ['Europe/London', '2026-10-25', 1792882800, 1792972800],
  ['UTC', '2026-03-08', 1772928000, 1773014400],
  ['UTC', '2026-11-01', 1793491200, 1793577600],
  ['UTC', '2026-03-29', 1774742400, 1774828800],
  ['UTC', '2026-10-25', 1792886400, 1792972800],
  ['Asia/Kathmandu', '2026-03-08', 1772907300, 1772993700],
  ['Asia/Kathmandu', '2026-11-01', 1793470500, 1793556900],
  ['Asia/Kathmandu', '2026-03-29', 1774721700, 1774808100],
  ['Asia/Kathmandu', '2026-10-25', 1792865700, 1792952100],
] as const

function compiled(file: string): string {
  return ts.transpileModule(
    readFileSync(new URL(file, import.meta.url), 'utf8'),
    {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
      },
    }
  ).outputText
}
const report = compiled('./reportModel.ts')
const snapshot = compiled('./snapshotModel.ts')

it.each(
  cases
)('calendar labels and chart bounds in %s on %s', (zone, day, start, end) => {
  // A subprocess gives Date a real OS zone, even with Vitest's worker threads.
  // Only presentation formatters are stubbed; the calendar functions are production code.
  const code = `
    const load = (source) => {
      const module = { exports: {} };
      new Function('module', 'exports', 'require', source)(module, module.exports,
        () => ({ formatClock: () => 'clock', formatDate: () => 'date', formatWeekdayClock: () => 'weekday' }));
      return module.exports;
    };
    const report = load(${JSON.stringify(report)});
    const snapshot = load(${JSON.stringify(snapshot)});
    console.log(JSON.stringify({
      early: report.formatReportDay(${start + 1800}, ${end + 43200}),
      prior: report.formatReportDay(${start - 1800}, ${end + 43200}),
      label: snapshot.relativeDay(${JSON.stringify(day)}, ${end + 43200}),
    }));
  `
  const result = JSON.parse(
    execFileSync(process.execPath, ['-e', code], {
      env: { ...process.env, TZ: zone },
      encoding: 'utf8',
    })
  )
  expect(result.early).toBe('Yesterday')
  expect(result.prior).not.toBe('Yesterday')
  expect(result.label).toBe('Yesterday')
  const href = databaseHref({
    target: 'db',
    period: { start: start, end: end },
    now: end + 43200,
  })
  expect(new URL(href, 'https://test.invalid').searchParams.get('zoom')).toBe(
    `${start}-${end}`
  )
})
