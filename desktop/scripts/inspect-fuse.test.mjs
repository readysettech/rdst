import { describe, expect, it } from "vitest";

import { enableInspectFuse } from "./inspect-fuse.mjs";

const SENTINEL = "dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX";
// A binary with fuse wires holding `states` (RunAsNode first), padded like a real one.
const binary = (states, copies = 1) =>
  Buffer.concat(
    Array.from({ length: copies }, () =>
      Buffer.concat([Buffer.from("ELF..."), Buffer.from(SENTINEL), Buffer.from([1, states.length]), Buffer.from(states)])
    )
  );
const wires = (buffer) => {
  const found = [];
  for (let at = buffer.indexOf(SENTINEL); at !== -1; at = buffer.indexOf(SENTINEL, at + 1)) {
    const start = at + SENTINEL.length + 2;
    found.push(buffer.subarray(start, start + buffer[start - 1]).toString());
  }
  return found;
};

describe("enableInspectFuse", () => {
  it("turns on only the inspect fuse of a release binary", () => {
    const release = binary("00101101r");
    expect(enableInspectFuse(release)).toBe(true);
    expect(wires(release)).toEqual(["00111101r"]);
    expect(enableInspectFuse(release)).toBe(false);
    expect(wires(release)).toEqual(["00111101r"]);
  });

  it("covers both wires of a binary that carries two", () => {
    const universal = binary("00101101", 2);
    expect(enableInspectFuse(universal)).toBe(true);
    expect(wires(universal)).toEqual(["00111101", "00111101"]);
  });

  it("refuses a binary it doesn't understand", () => {
    expect(() => enableInspectFuse(Buffer.from("no wire here"))).toThrow("no Electron fuse wire");
    const future = binary("00101101");
    future[future.indexOf(SENTINEL) + SENTINEL.length] = 2;
    expect(() => enableInspectFuse(future)).toThrow("unsupported fuse wire version 2");
    expect(() => enableInspectFuse(binary("001"))).toThrow("no inspect fuse");
    expect(() => enableInspectFuse(binary("000r"))).toThrow("unexpected inspect fuse state");
  });
});
