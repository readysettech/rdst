import { describe, expect, it } from "vitest";

import { createCsvFilename, toCsv } from "./csv";

describe("toCsv", () => {
  it("serializes simple columns and rows", () => {
    const csv = toCsv(
      ["name", "count"],
      [
        ["alice", 1],
        ["bob", 2],
      ],
    );

    expect(csv).toBe("name,count\r\nalice,1\r\nbob,2");
  });

  it("quotes values containing commas", () => {
    const csv = toCsv(["name"], [["last, first"]]);
    expect(csv).toBe("name\r\n\"last, first\"");
  });

  it("escapes embedded quotes", () => {
    const csv = toCsv(["quote"], [["say \"hello\""]]);
    expect(csv).toBe("quote\r\n\"say \"\"hello\"\"\"");
  });

  it("quotes multiline values", () => {
    const csv = toCsv(["notes"], [["line 1\nline 2"]]);
    expect(csv).toBe("notes\r\n\"line 1\nline 2\"");
  });

  it("writes null and undefined as empty cells", () => {
    const csv = toCsv(["a", "b", "c"], [[null, undefined, "x"]]);
    expect(csv).toBe("a,b,c\r\n,,x");
  });

  it("handles empty rows", () => {
    const csv = toCsv(["a", "b"], []);
    expect(csv).toBe("a,b");
  });

  it("converts non-string primitives safely", () => {
    const csv = toCsv(["bool", "bigint"], [[true, BigInt(42)]]);
    expect(csv).toBe("bool,bigint\r\ntrue,42");
  });
});

describe("createCsvFilename", () => {
  it("formats timestamp as YYYYMMDD-HHmmss", () => {
    const filename = createCsvFilename(new Date(2025, 0, 2, 3, 4, 5));
    expect(filename).toBe("rdst-query-results-20250102-030405.csv");
  });
});
