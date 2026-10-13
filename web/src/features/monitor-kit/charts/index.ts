// Monitor chart kit (brief section 4). Import from '@/features/monitor-kit/charts'.
//   MonitorTimeChart  large time chart with markers, gaps, crosshair, syncId
//   MiniTrend         40px sparkline for tiles, rows and inbox items
//   highlight         a shaded incident window, per chart or for a whole page
//   StackedLoadChart  'Where the time goes': stacked bands per query, band hover/select
//   overlayModel      the highlighted query: its band, or its own line over the stack
//   loadModel         load per query from /queries series (loadFromQueries, formatPercent, timeShareRows)
//   rangeModel        availableRanges, clampRange, domainFor, resolutionFor, ticksFor...
//   chartTheme        SERIES_COLOR, TONE_COLOR
//   markers           markerTone, splitMarkers, clusterMarkers, MarkerGlyphIcon
export { useHoverX } from './chartHooks'
export {
  type ChartPoint,
  isPartialBucket,
  niceMax,
  yDomain,
} from './chartMath'
export {
  type ChartTone,
  type MonitorMetric,
  SERIES_COLOR,
  TONE_COLOR,
} from './chartTheme'
export {
  type ChartHighlight,
  ChartHighlightContext,
  HighlightBand,
  useChartHighlight,
} from './highlight'
export {
  BAND_COLORS,
  bandColors,
  buildLoadModel,
  type Coverage,
  formatPercent,
  type LoadBandModel,
  type LoadMetric,
  type LoadModel,
  loadFromBreakdown,
  loadFromQueries,
  OTHER_COLOR,
  OTHER_KEY,
  type QueryScope,
  stackMax,
  timeShareRows,
} from './loadModel'
export { MiniTrend, type MiniTrendProps } from './MiniTrend'
export {
  type MonitorSeries,
  MonitorTimeChart,
  type MonitorTimeChartProps,
} from './MonitorTimeChart'
export {
  clusterGap,
  clusterMarkers,
  isIndexMarker,
  MarkerGlyphIcon,
  markerKindName,
  markerTone,
  splitMarkers,
} from './markers'
export {
  buildOverlay,
  type ChartOverlay,
  type HighlightPick,
  OVERLAY_COLOR,
  overlayValues,
  pickHighlight,
} from './overlayModel'
export {
  type ChartPause,
  chartPauses,
  PAUSE_LABEL,
  type PauseCause,
  pauseAt,
} from './pauseModel'
export * from './rangeModel'
export {
  type ChartMarkLine,
  StackedLoadChart,
  type StackedLoadChartProps,
  type StackSeries,
} from './StackedLoadChart'
