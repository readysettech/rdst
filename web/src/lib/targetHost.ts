/**
 * Loopback / same-host detection for target connection hosts (B5).
 *
 * A target whose host is loopback (or a local Docker/unix socket) is "local";
 * anything else is treated as remote so the benchmark rail can flag it and
 * require a stronger confirmation before running load against it.
 */

const LOCAL_HOSTS = new Set([
  "localhost",
  "127.0.0.1",
  "::1",
  "0.0.0.0",
  "host.docker.internal",
]);

export function isRemoteTargetHost(host?: string | null): boolean {
  if (!host) return false; // no host configured → local socket / unset
  const h = host.trim().toLowerCase();
  if (!h) return false;
  if (h.startsWith("/")) return false; // unix domain socket path
  return !LOCAL_HOSTS.has(h);
}
