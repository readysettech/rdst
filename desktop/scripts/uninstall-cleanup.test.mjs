import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, existsSync, symlinkSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const builderConfig = readFileSync(resolve(root, "electron-builder.yml"), "utf8");
const installer = readFileSync(resolve(root, "resources/installer.nsh"), "utf8");

const cleanup = readFileSync(resolve(root, "resources/uninstall-cleanup.ps1"), "utf8");

const macro = installer.match(/!macro customUnInstall\n([\s\S]*?)\n!macroend/)?.[1] ?? "";
const lines = macro.split("\n").map((line) => line.trim());
const guardAt = lines.indexOf("${ifNot} ${isUpdated}");

describe("Windows uninstall cleanup", () => {
  it("is wired into the NSIS target", () => {
    const nsis = builderConfig.match(/^nsis:\n((?: {2}.*\n)+)/m)?.[1] ?? "";
    expect(nsis).toMatch(/^ {2}include: resources\/installer\.nsh$/m);
    expect(nsis).toMatch(/^ {2}deleteAppDataOnUninstall: true$/m);
    expect(builderConfig).toContain('from: resources/uninstall-cleanup.ps1');
    expect(macro).toContain('$INSTDIR\\resources\\uninstall-cleanup.ps1');
  });

  it("deletes only for a real uninstall, never for an update", () => {
    expect(guardAt).toBeGreaterThanOrEqual(0);
    const body = lines.slice(guardAt + 1, lines.lastIndexOf("${endIf}"));
    expect(lines.filter((l) => l === "${ifNot} ${isUpdated}")).toHaveLength(1);
    // The guard opens the macro and its closing endIf is the last statement.
    expect(lines.slice(0, guardAt).filter((l) => l && !l.startsWith(";"))).toEqual([]);
    expect(lines.at(-1)).toBe("${endIf}");
    const destructive = lines.filter((l) => /^(RMDir|DeleteReg|nsExec)/.test(l));
    expect(destructive.length).toBeGreaterThan(0);
    for (const line of destructive) expect(body).toContain(line);
  });

  it("removes the sidecar data directory and Electron's folders", () => {
    expect(cleanup).toContain("$env:RDST_DATA_DIR");
    expect(macro).toContain('RMDir /r "$R0\\.rdst"');
    expect(macro).toContain('RMDir /r "$APPDATA\\rdst-desktop"');
    expect(macro).toContain('RMDir /r "$LOCALAPPDATA\\rdst-desktop-updater"');
  });

  it("removes the start-at-login value Electron names after the AppUserModelID", () => {
    const run = 'DeleteRegValue HKCU "Software\\Microsoft\\Windows\\CurrentVersion\\Run"';
    expect(macro).toContain(`${run} "com.readyset.rdst-desktop"`);
  });

  it("uses locale-independent native credential names and exact service boundaries", () => {
    expect(cleanup).toContain('EntryPoint="CredEnumerateW"');
    expect(cleanup).toContain('EntryPoint="CredDeleteW"');
    expect(cleanup).not.toContain('cmdkey');
    const pattern = new RegExp(cleanup.match(/-cmatch '([^']+)'/)[1]);
    const names = [
      'RDST_ACCOUNT_ACCESS_TOKEN', 'RDST_ACCOUNT_REFRESH_TOKEN', 'RDST_ACCOUNT_EXPIRES_AT',
      'ANTHROPIC_API_KEY', 'DB_PASSWORD', 'T123:bot-token', 'T123:app-token',
      'T123:version:bot-token', 'T123:version:app-token',
    ];
    for (const service of ['rdst-web', 'rdst-slack']) {
      expect(pattern.test(service)).toBe(true);
      for (const name of names) {
        for (const entry of [name, `${name}#1`, `${name}#2`, `${name}#64`]) {
          expect(pattern.test(`${entry}@${service}`)).toBe(true);
          expect(pattern.test(`${service}@${entry}`)).toBe(true);
        }
      }
    }
    for (const target of [
      'token@rdst-webhook', 'rdst-webhook@token', 'rdst-web.corp', 'rdst-slackbot@token',
      'token#1@rdst-slackbot', 'rdst-web@', '@rdst-web', 'git:https://github.com',
    ]) {
      expect(pattern.test(target)).toBe(false);
    }
    expect(cleanup).toContain('$credential.Type -eq 1');
  });

  it("passes a native null filter to enumerate the entire current user vault", () => {
    expect(cleanup).toContain('Enumerate(IntPtr filter,');
    expect(cleanup).toContain('[RdstCredentials]::Enumerate([IntPtr]::Zero, 0,');
    expect(cleanup).toContain('[RdstCredentials]::CredFree($credentials)');
  });

  it.skipIf(process.platform !== 'win32')('executes the ownership guard on populated custom paths', () => {
    const parent = mkdtempSync(resolve(tmpdir(), 'rdst-uninstall-test-'));
    const owned = resolve(parent, 'owned with spaces \u03bb');
    const unowned = resolve(parent, 'unowned with spaces');
    const invalid = resolve(parent, 'invalid marker');
    const external = resolve(parent, 'external directory');
    for (const dir of [owned, unowned, invalid, external]) {
      mkdirSync(dir);
      writeFileSync(resolve(dir, 'sentinel'), 'keep unless owned');
    }
    writeFileSync(resolve(owned, '.rdst-owned'), 'rdst-desktop-data-v1\n');
    writeFileSync(resolve(invalid, '.rdst-owned'), 'not an ownership marker');
    const junction = resolve(parent, 'linked directory');
    writeFileSync(resolve(external, '.rdst-owned'), 'rdst-desktop-data-v1\n');
    symlinkSync(external, junction, 'junction');
    const script = resolve(parent, 'check.ps1');
    const quote = (value) => "'" + value.replaceAll("'", "''") + "'";
    writeFileSync(script, `\ufeff. ${quote(resolve(root, 'resources/uninstall-cleanup.ps1'))}
      Remove-RdstOwnedDirectory ${quote(unowned)}
      Remove-RdstOwnedDirectory ${quote(invalid)}
      Remove-RdstOwnedDirectory ${quote(junction)}
      Remove-RdstOwnedDirectory (Get-Item -LiteralPath ${quote(parent)}).PSDrive.Root
      Remove-RdstOwnedDirectory ${quote(owned)}
    `);
    execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script], { timeout: 15000 });
    expect(existsSync(resolve(unowned, 'sentinel'))).toBe(true);
    expect(existsSync(resolve(invalid, 'sentinel'))).toBe(true);
    expect(existsSync(junction)).toBe(true);
    expect(existsSync(resolve(external, 'sentinel'))).toBe(true);
    expect(existsSync(owned)).toBe(false);
  });

  it("requires explicit ownership and rejects roots and reparse points", () => {
    expect(macro).not.toContain('RMDir /r "$R0"');
    expect(cleanup).toContain("'.rdst-owned'");
    expect(cleanup).toContain('$directory.Parent');
    expect(cleanup).toContain('ReparsePoint');
    expect(cleanup).toContain('-ceq "rdst-desktop-data-v1`n"');
    expect(cleanup).toContain('Remove-Item -LiteralPath');
  });
});
