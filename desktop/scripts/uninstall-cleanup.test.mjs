import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, existsSync, symlinkSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { flavorFacts } from "./flavor.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const builderConfig = readFileSync(resolve(root, "electron-builder.yml"), "utf8");
const installer = readFileSync(resolve(root, "resources/installer.nsh"), "utf8");

const cleanup = readFileSync(resolve(root, "resources/uninstall-cleanup.ps1"), "utf8");

const macro = installer.match(/!macro customUnInstall\n([\s\S]*?)\n!macroend/)?.[1] ?? "";
const lines = macro.split("\n").map((line) => line.trim());
const guardAt = lines.indexOf("${ifNot} ${isUpdated}");

// The compile-time table in installer.nsh: APP_ID -> the defines its !if branch sets.
const flavors = {};
let branch = null;
for (const line of installer.split("\n").map((l) => l.trim())) {
  const opened = line.match(/^!(?:else )?if "\$\{APP_ID\}" == "([^"]+)"$/);
  if (opened) {
    branch = flavors[opened[1]] = {};
  } else if (/^!(?:else|endif)\b/.test(line)) {
    branch = null;
  } else if (branch) {
    const define = line.match(/^!define (\w+) "([^"]*)"$/);
    if (define) branch[define[1]] = define[2];
  }
}

// The macro as makensis compiles it for one flavor, with electron-builder's defines.
function compiled(appId, productName) {
  const defines = { APP_ID: appId, PRODUCT_NAME: productName, ...flavors[appId] };
  defines.RDST_CLEANUP_ARGS = defines.RDST_INSTANCE ? ` -Instance ${defines.RDST_INSTANCE}` : "";
  const expanded = lines.map((line) => line.replace(/\$\{(\w+)\}/g, (m, name) => defines[name] ?? m));
  return expanded.filter((l) => /^(RMDir|DeleteReg|nsExec)/.test(l));
}

const run = String.raw`DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run"`;
const approved = String.raw`DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"`;
const powershell = "nsExec::Exec `" + String.raw`"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\uninstall-cleanup.ps1"`;
const productionCleanup = [
  powershell + "`",
  String.raw`RMDir /r "$R0\.rdst"`,
  String.raw`RMDir /r "$APPDATA\rdst-desktop"`,
  String.raw`RMDir /r "$APPDATA\RDST Desktop"`,
  String.raw`RMDir /r "$LOCALAPPDATA\rdst-desktop-updater"`,
  ...[run, approved].flatMap((key) => [
    `${key} "com.readyset.rdst-desktop"`, `${key} "RDST Desktop"`, `${key} "rdst-desktop"`,
  ]),
];
const clCleanup = [
  powershell + " -Instance cl`",
  String.raw`RMDir /r "$R0\.rdst-cl"`,
  String.raw`RMDir /r "$APPDATA\rdst-desktop-cl"`,
  String.raw`RMDir /r "$APPDATA\RDST Desktop CL"`,
  String.raw`RMDir /r "$LOCALAPPDATA\rdst-desktop-cl-updater"`,
  ...[run, approved].flatMap((key) => [
    `${key} "com.readyset.rdst-desktop.cl"`, `${key} "RDST Desktop CL"`, `${key} "rdst-desktop-cl"`,
  ]),
];

const credentialTemplate = cleanup.match(/'(\^[^']+)' -f \$suffix/)?.[1] ?? "";
const credentialPattern = (instance) =>
  new RegExp(credentialTemplate.replaceAll("{0}", instance ? `-${instance}` : ""));
const credentialNames = [
  'RDST_ACCOUNT_ACCESS_TOKEN', 'RDST_ACCOUNT_REFRESH_TOKEN', 'RDST_ACCOUNT_EXPIRES_AT',
  'ANTHROPIC_API_KEY', 'DB_PASSWORD', 'T123:bot-token', 'T123:app-token',
  'T123:version:bot-token', 'T123:version:app-token',
];
const credentialTargets = (service) => [service, ...credentialNames.flatMap((name) =>
  [name, `${name}#1`, `${name}#2`, `${name}#64`].flatMap((entry) => [`${entry}@${service}`, `${service}@${entry}`]))];

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
    expect(macro).toContain('RMDir /r "$R0\\${RDST_DATA_DIR_NAME}"');
    expect(macro).toContain('RMDir /r "$APPDATA\\${RDST_ELECTRON_NAME}"');
    expect(macro).toContain('RMDir /r "$LOCALAPPDATA\\${RDST_ELECTRON_NAME}-updater"');
  });

  it("removes the start-at-login value Electron names after the AppUserModelID", () => {
    expect(macro).toContain(`${run} "\${APP_ID}"`);
  });

  it("compiles production's cleanup exactly as before and CL's with only CL names", () => {
    expect(Object.keys(flavors)).toEqual([
      "com.readyset.rdst-desktop", "com.readyset.rdst-desktop.cl", "com.readyset.rdst-desktop.staging",
    ]);
    expect(installer).toMatch(/^!else\n {2}!error "[^"]*\$\{APP_ID\}"\n!endif$/m);
    expect(installer).toContain('!define RDST_CLEANUP_ARGS " -Instance ${RDST_INSTANCE}"');
    expect(compiled("com.readyset.rdst-desktop", "RDST Desktop")).toEqual(productionCleanup);
    expect(compiled("com.readyset.rdst-desktop.cl", "RDST Desktop CL")).toEqual(clCleanup);
    for (const line of clCleanup.slice(1)) expect(productionCleanup).not.toContain(line);
  });

  it("removes a custom RDST_DATA_DIR only for production, which every flavor may share", () => {
    expect(cleanup).toContain("if (-not $Instance) { Remove-RdstOwnedDirectory $env:RDST_DATA_DIR }");
  });

  it("maps every Windows flavor's electron-builder identity", () => {
    for (const flavor of ["release", "cl", "staging"]) {
      const facts = flavorFacts(flavor, {});
      const instance = facts.RDST_DESKTOP_INSTANCE;
      expect(flavors[facts.RDST_DESKTOP_APP_ID], flavor).toEqual({
        ...(instance ? { RDST_INSTANCE: instance } : {}),
        RDST_DATA_DIR_NAME: instance ? `.rdst-${instance}` : ".rdst",
        RDST_ELECTRON_NAME: facts.RDST_DESKTOP_NAME,
      });
    }
  });

  it("uses locale-independent native credential names and exact service boundaries", () => {
    expect(cleanup).toContain('EntryPoint="CredEnumerateW"');
    expect(cleanup).toContain('EntryPoint="CredDeleteW"');
    expect(cleanup).not.toContain('cmdkey');
    expect(cleanup).toContain('-cmatch $pattern');
    const pattern = credentialPattern("");
    for (const service of ['rdst-web', 'rdst-slack']) {
      for (const target of credentialTargets(service)) expect(pattern.test(target), target).toBe(true);
    }
    for (const target of [
      'token@rdst-webhook', 'rdst-webhook@token', 'rdst-web.corp', 'rdst-slackbot@token',
      'token#1@rdst-slackbot', 'rdst-web@', '@rdst-web', 'git:https://github.com',
    ]) {
      expect(pattern.test(target)).toBe(false);
    }
    expect(cleanup).toContain('$credential.Type -eq 1');
  });

  it("keeps each flavor's credentials out of the other's uninstall", () => {
    const production = credentialPattern("");
    const cl = credentialPattern("cl");
    for (const service of ['rdst-web-cl', 'rdst-slack-cl']) {
      for (const target of credentialTargets(service)) {
        expect(cl.test(target), target).toBe(true);
        expect(production.test(target), target).toBe(false);
      }
    }
    for (const service of ['rdst-web', 'rdst-slack', 'rdst-web-staging', 'rdst-slack-staging']) {
      for (const target of credentialTargets(service)) expect(cl.test(target), target).toBe(false);
    }
    for (const target of ['token@rdst-web-clx', 'rdst-web-cl.corp', 'token@rdst-webhook-cl', '@rdst-web-cl']) {
      expect(cl.test(target), target).toBe(false);
    }
  });

  it("accepts only a well-formed instance", () => {
    expect(cleanup).toMatch(/^param\(\[string\]\$Instance = ''\)$/m);
    expect(cleanup).toContain("$Instance -cnotmatch '\\A[a-z][a-z0-9-]{0,31}\\z'");
    expect(cleanup).toContain('Remove-RdstCredentials $Instance');
  });

  it.skipIf(process.platform !== 'win32')('builds the per-instance credential pattern in PowerShell', () => {
    const parent = mkdtempSync(resolve(tmpdir(), 'rdst-uninstall-pattern-'));
    const script = resolve(parent, 'check.ps1');
    const quote = (value) => "'" + value.replaceAll("'", "''") + "'";
    writeFileSync(script, `\ufeff. ${quote(resolve(root, 'resources/uninstall-cleanup.ps1'))}
      $production = Get-RdstCredentialPattern ''
      $cl = Get-RdstCredentialPattern 'cl'
      $checks = @(
        ('T1@rdst-web' -cmatch $production), -not ('T1@rdst-web-cl' -cmatch $production),
        ('T1#1@rdst-web-cl' -cmatch $cl), -not ('T1@rdst-web' -cmatch $cl), ('rdst-slack-cl' -cmatch $cl)
      )
      foreach ($bad in @('CL', 'c l', '-cl', "cl\`n")) {
        try { Get-RdstCredentialPattern $bad | Out-Null; $checks += $false } catch { $checks += $true }
      }
      if ($checks -contains $false) { exit 1 }
    `);
    execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script], { timeout: 15000 });
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
