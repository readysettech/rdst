// Turns the EnableNodeCliInspectArguments fuse on in a Linux RDST Desktop binary that a test
// harness unpacked or installed for itself. Playwright drives the app through --inspect, which
// flavors that ship to users have fused off (electron-builder.yml); every other fuse and byte
// stays as shipped.
//
//   node scripts/inspect-fuse.mjs <electron-binary>
import { readFileSync, realpathSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

// The @electron/fuses V1 wire: this sentinel, a version byte, a length byte, then one state
// byte per fuse in FuseV1Options order.
const SENTINEL = Buffer.from("dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX");
const WIRE_VERSION = 1;
const INSPECT_FUSE = 3;
const DISABLED = 0x30;
const ENABLED = 0x31;

/** Enables the inspect fuse in `binary` in place; true when it was off. */
export function enableInspectFuse(binary) {
  const first = binary.indexOf(SENTINEL);
  if (first === -1) throw new Error("no Electron fuse wire in this binary");
  let changed = false;
  for (const at of new Set([first, binary.lastIndexOf(SENTINEL)])) {
    const wire = at + SENTINEL.length;
    if (binary[wire] !== WIRE_VERSION) throw new Error(`unsupported fuse wire version ${binary[wire]}`);
    if (binary[wire + 1] <= INSPECT_FUSE) throw new Error("the fuse wire has no inspect fuse");
    const state = wire + 2 + INSPECT_FUSE;
    if (binary[state] !== ENABLED && binary[state] !== DISABLED) {
      throw new Error(`unexpected inspect fuse state ${binary[state]}`);
    }
    changed ||= binary[state] === DISABLED;
    binary[state] = ENABLED;
  }
  return changed;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const target = process.argv[2];
  if (!target) throw new Error("usage: inspect-fuse.mjs <electron-binary>");
  const file = realpathSync(target);
  const binary = readFileSync(file);
  if (enableInspectFuse(binary)) {
    writeFileSync(file, binary);
    console.log(`Enabled the --inspect fuse in ${file}`);
  } else {
    console.log(`The --inspect fuse is already on in ${file}`);
  }
}
