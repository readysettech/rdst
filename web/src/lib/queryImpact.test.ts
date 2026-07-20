import { describe, expect, it } from "vitest";

import type { QueryRegistryEntry } from "./api";
import {
  byImpact,
  formatDbTime,
  formatImpactCaption,
  formatRunCount,
  isHeroCandidate,
  queryImpactMs,
} from "./queryImpact";

function mk(partial: Partial<QueryRegistryEntry>): QueryRegistryEntry {
  return {
    sql: "SELECT 1",
    hash: "h",
    tag: "",
    last_analyzed: "2026-01-01",
    target: "db",
    frequency: 0,
    source: "manual",
    ...partial,
  } as QueryRegistryEntry;
}

describe("queryImpactMs", () => {
  it("multiplies average latency by observation count", () => {
    expect(queryImpactMs(mk({ avg_duration_ms: 142, observation_count: 4200 }))).toBe(596400);
  });

  it("is 0 when either factor is missing", () => {
    expect(queryImpactMs(mk({ avg_duration_ms: 142 }))).toBe(0);
    expect(queryImpactMs(mk({ observation_count: 4200 }))).toBe(0);
    expect(queryImpactMs(mk({}))).toBe(0);
  });
});

describe("byImpact", () => {
  it("orders by impact descending and keeps the zero tail in incoming order", () => {
    const a = mk({ hash: "a", avg_duration_ms: 10, observation_count: 10 }); // 100
    const b = mk({ hash: "b", avg_duration_ms: 100, observation_count: 100 }); // 10000
    const r1 = mk({ hash: "r1" }); // 0, first in
    const r2 = mk({ hash: "r2" }); // 0, second in
    const sorted = [r1, a, r2, b].sort(byImpact).map((e) => e.hash);
    expect(sorted).toEqual(["b", "a", "r1", "r2"]);
  });
});

describe("formatDbTime", () => {
  it("scales ms then s then min then h", () => {
    expect(formatDbTime(450)).toBe("450ms");
    expect(formatDbTime(2500)).toBe("2.5s");
    expect(formatDbTime(90_000)).toBe("1.5 min");
    expect(formatDbTime(5_400_000)).toBe("1.5 h");
  });
});

describe("captions", () => {
  it("formats the impact headline and run count when measured", () => {
    const e = mk({ avg_duration_ms: 142, observation_count: 4200 }); // 596400ms = 9.9 min
    expect(formatImpactCaption(e)).toBe("9.9 min DB time");
    expect(formatRunCount(e)).toBe("4,200 runs");
  });

  it("returns null with no telemetry", () => {
    expect(formatImpactCaption(mk({}))).toBeNull();
    expect(formatRunCount(mk({ observation_count: 0 }))).toBeNull();
  });
});

describe("isHeroCandidate", () => {
  const impactful = { avg_duration_ms: 142, observation_count: 4200 };

  it("is true for an uncached, measured, cacheable-or-unknown query", () => {
    expect(isHeroCandidate(mk({ ...impactful, readyset_supported: "yes" }), false)).toBe(true);
    expect(isHeroCandidate(mk({ ...impactful, readyset_supported: "" }), false)).toBe(true);
  });

  it("is false when already cached", () => {
    expect(isHeroCandidate(mk({ ...impactful }), true)).toBe(false);
  });

  it("is false when known-uncacheable", () => {
    expect(isHeroCandidate(mk({ ...impactful, readyset_supported: "unsupported: random()" }), false)).toBe(false);
  });

  it("is false without measured impact", () => {
    expect(isHeroCandidate(mk({ readyset_supported: "yes" }), false)).toBe(false);
  });
});
