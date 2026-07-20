import { describe, expect, it } from "vitest";

import { fillCapturedParams } from "./sqlParameters";

describe("fillCapturedParams", () => {
  it("fills named placeholders from captured values, in any order", () => {
    const sql = "SELECT count(*) FROM t WHERE rating > :p2 LIMIT :p1";
    expect(fillCapturedParams(sql, { p1: "100", p2: "8.5" })).toBe(
      "SELECT count(*) FROM t WHERE rating > 8.5 LIMIT 100",
    );
  });

  it("fills positional $N placeholders", () => {
    const sql = "SELECT * FROM t WHERE a > $1 AND b < $2";
    expect(fillCapturedParams(sql, { p1: "5", p2: "10" })).toBe(
      "SELECT * FROM t WHERE a > 5 AND b < 10",
    );
  });

  it("quotes non-numeric string values", () => {
    expect(fillCapturedParams("SELECT * FROM t WHERE name = :p1", { p1: "Alice" })).toBe(
      "SELECT * FROM t WHERE name = 'Alice'",
    );
  });

  it("returns the SQL unchanged when there are no parameters", () => {
    const sql = "SELECT count(*) FROM t WHERE rating > 8.5";
    expect(fillCapturedParams(sql, { p1: "x" })).toBe(sql);
  });

  it("leaves placeholders untouched when no captured values exist", () => {
    const sql = "SELECT * FROM t WHERE a > :p1";
    expect(fillCapturedParams(sql, {})).toBe(sql);
    expect(fillCapturedParams(sql, undefined)).toBe(sql);
  });

  it("fills only the placeholders it has values for, leaving the rest", () => {
    const sql = "SELECT * FROM t WHERE a > :p1 AND b < :p2";
    expect(fillCapturedParams(sql, { p1: "5" })).toBe(
      "SELECT * FROM t WHERE a > 5 AND b < :p2",
    );
  });
});
