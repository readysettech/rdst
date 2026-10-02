// Contents of rdst/_version_build.py, the build metadata baked into a sidecar.
// keyserviceUrl is set only by CL builds, which point their binaries at the
// CL's own keyservice; shared/keyservice.py falls back to production without it.
export function versionBuildSource({ major, minor, version, keyserviceUrl }) {
  const lines = [
    '"""Generated build metadata for an RDST Desktop package build."""',
    "",
    `MAJOR = ${major}`,
    `MINOR = ${minor}`,
    "",
    `__version__ = ${JSON.stringify(version)}`,
    "__version_info__ = (MAJOR, MINOR)",
    ""
  ];
  if (keyserviceUrl) {
    lines.push(`KEYSERVICE_URL = ${JSON.stringify(keyserviceUrl)}`, "");
  }
  return lines.join("\n");
}
