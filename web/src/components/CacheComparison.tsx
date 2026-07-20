import { Text } from "@rs/ui-new/text";
import { Icon } from "@rs/ui-new/icon";
import { HStack } from "@rs/ui-new/stack";
import { m } from "@rs/ui-new/motion";
import type { CacheRunResult } from "../types/cache";

export function formatMs(ms: number): string {
  if (ms < 1) return "<1ms";
  if (ms < 1000) return `${ms.toFixed(1)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

/** Visual latency bar — width proportional to value relative to max. */
function LatencyBar({
  label,
  value,
  maxValue,
  variant,
  delay = 0,
}: {
  label: string;
  value: number;
  maxValue: number;
  variant: "origin" | "cache-win" | "cache-lose";
  delay?: number;
}) {
  const pct = maxValue > 0 ? Math.max((value / maxValue) * 100, 2) : 2;
  const barColor =
    variant === "cache-win"
      ? "bg-surface-positive-solid"
      : variant === "origin"
        ? "bg-content-layout-3/40"
        : "bg-surface-warning-solid/70";
  const textColor =
    variant === "cache-win" ? "text-content-positive-soft" : "text-content-layout-1";

  return (
    <div className="flex items-center gap-3">
      <Text level="caption" className="text-content-layout-3 w-8 text-right shrink-0">
        {label}
      </Text>
      <div className="flex-1 h-6 bg-surface-layout-2/50 rounded-md overflow-hidden relative">
        <m.div
          className={`h-full rounded-md ${barColor}`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] }}
        />
      </div>
      <Text level="mono-small" className={`w-16 text-right shrink-0 tabular-nums ${textColor}`}>
        {formatMs(value)}
      </Text>
    </div>
  );
}

// Origin-vs-cache before/after card. Layout-agnostic (no table/card wrapper) so
// it can drop into the Cache page's table and the Queries workbench alike. Shows
// the outcome honestly whether the cache wins or loses (rdst-41p.4).
export function ComparisonCard({
  result,
  onDismiss,
}: {
  result: CacheRunResult;
  onDismiss?: () => void;
}) {
  const isWinner = result.winner === "readyset";
  const maxLatency = Math.max(
    result.origin_stats.mean,
    result.origin_stats.p50,
    result.origin_stats.p95,
    result.cache_stats.mean,
    result.cache_stats.p50,
    result.cache_stats.p95,
  );
  const speedupDisplay = isWinner
    ? `${result.speedup_mean.toFixed(1)}x`
    : `${Math.abs(result.improvement_pct).toFixed(0)}%`;

  return (
    <m.div
      className="rounded-xl border border-border-layout-1 bg-surface-layout-1/80 overflow-hidden"
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      {/* Header strip */}
      <div
        className={`px-5 py-3 flex items-center justify-between ${
          isWinner
            ? "bg-surface-positive-soft/15 border-b border-border-positive-soft/30"
            : "bg-surface-warning-soft/10 border-b border-border-warning-soft/30"
        }`}
      >
        <HStack className="gap-3 items-center">
          {/* Speedup badge */}
          <m.div
            className={`flex items-center justify-center rounded-lg px-3 py-1.5 font-mono text-sm font-medium tracking-tight ${
              isWinner ? "bg-surface-positive-solid text-white" : "bg-surface-warning-solid text-white"
            }`}
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.3, delay: 0.2 }}
          >
            {isWinner ? <>{speedupDisplay} faster</> : <>{speedupDisplay} slower</>}
          </m.div>
          <Text level="label-small" className="text-content-layout-2">
            {isWinner
              ? "ReadySet cache outperforms origin"
              : "Origin is faster — cache may need warming"}
          </Text>
        </HStack>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="p-1 rounded-md hover:bg-surface-layout-2 transition-colors text-content-layout-3 hover:text-content-layout-1 cursor-pointer"
          >
            <Icon name="close" label="Dismiss" className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* Comparison bars */}
      <div className="px-5 py-4 grid grid-cols-2 gap-6">
        {/* Origin column */}
        <div className="space-y-2">
          <HStack className="gap-2 items-center mb-1">
            <div className="w-2 h-2 rounded-full bg-content-layout-3/40" />
            <Text level="overline" className="text-content-layout-3 uppercase tracking-widest text-[10px]">
              Origin
            </Text>
          </HStack>
          <LatencyBar label="Mean" value={result.origin_stats.mean} maxValue={maxLatency} variant="origin" delay={0.1} />
          <LatencyBar label="P50" value={result.origin_stats.p50} maxValue={maxLatency} variant="origin" delay={0.15} />
          <LatencyBar label="P95" value={result.origin_stats.p95} maxValue={maxLatency} variant="origin" delay={0.2} />
        </div>

        {/* Cache column */}
        <div className="space-y-2">
          <HStack className="gap-2 items-center mb-1">
            <div
              className={`w-2 h-2 rounded-full ${isWinner ? "bg-surface-positive-solid" : "bg-surface-warning-solid/70"}`}
            />
            <Text level="overline" className="text-content-layout-3 uppercase tracking-widest text-[10px]">
              ReadySet
            </Text>
          </HStack>
          <LatencyBar
            label="Mean"
            value={result.cache_stats.mean}
            maxValue={maxLatency}
            variant={isWinner ? "cache-win" : "cache-lose"}
            delay={0.25}
          />
          <LatencyBar
            label="P50"
            value={result.cache_stats.p50}
            maxValue={maxLatency}
            variant={isWinner ? "cache-win" : "cache-lose"}
            delay={0.3}
          />
          <LatencyBar
            label="P95"
            value={result.cache_stats.p95}
            maxValue={maxLatency}
            variant={isWinner ? "cache-win" : "cache-lose"}
            delay={0.35}
          />
        </div>
      </div>

      {/* Footer */}
      <div className="px-5 py-2 border-t border-border-layout-1/50 flex items-center justify-between">
        <Text level="caption" className="text-content-layout-3">
          {result.iterations} iterations &middot; min{" "}
          {formatMs(Math.min(result.origin_stats.min, result.cache_stats.min))} &middot; max{" "}
          {formatMs(Math.max(result.origin_stats.max, result.cache_stats.max))}
        </Text>
        <HStack className="gap-4">
          {(["min", "max", "p99"] as const).map((stat) => (
            <HStack key={stat} className="gap-1.5 items-center">
              <Text level="caption" className="text-content-layout-3 uppercase text-[10px]">
                {stat}
              </Text>
              <Text level="mono-small" className="text-content-layout-2 tabular-nums text-xs">
                {formatMs(result.cache_stats[stat])}
              </Text>
            </HStack>
          ))}
        </HStack>
      </div>
    </m.div>
  );
}
