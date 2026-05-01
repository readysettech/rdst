"""RDST Deploy command — deploy Readyset cache.

Delegates deployment logic to CacheService; this module handles CLI
rendering (Rich panels) and RdstResult conversion.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional
from urllib.parse import urlparse

from shared.cli.types import RdstResult
from shared.password_resolver import resolve_password_value
from shared.ui import Icons, StyledPanel, get_console

from ..events import CacheDeployCompleteEvent
from ..models import CacheInput, CacheOptions
from ..service import CacheService
from shared.service_events import ErrorEvent, ProgressEvent


def _parse_memory(value: str) -> int:
    """Parse a memory spec like '4g', '512m', '1g', or raw bytes int.

    Returns bytes. Raises ValueError on invalid input.
    """
    if not value:
        raise ValueError("Empty memory value")
    s = str(value).strip().lower()
    multipliers = {"k": 1024, "m": 1024**2, "g": 1024**3}
    if s and s[-1] in multipliers:
        return int(float(s[:-1]) * multipliers[s[-1]])
    # Plain integer = bytes
    return int(s)


class DeployCommand:
    """Deploy Readyset shallow cache permanently."""

    def execute(
        self,
        target: Optional[str] = None,
        mode: str = "docker",
        deploy_config: str = "readyset",
        host: Optional[str] = None,
        ssh_key: Optional[str] = None,
        ssh_user: str = "root",
        port: Optional[int] = None,
        namespace: str = "readyset",
        kubeconfig: Optional[str] = None,
        script_only: bool = False,
        output_json: bool = False,
        quiet: bool = False,
        no_request_path: bool = False,
        memory: Optional[str] = None,
        cpus: Optional[str] = None,
        force: bool = False,
    ) -> RdstResult:
        if not target:
            return RdstResult(False, "Target is required. Use: rdst cache deploy --target <name> --mode <mode>")

        # Script-only mode stays in CLI (no service needed)
        if script_only:
            return self._handle_script_only(target, mode, deploy_config, port, namespace, output_json, no_request_path)

        # Probe existing state before deploy. For modes with a real probe,
        # refuse to deploy over a running or stopped container — unless --force
        # is set, in which case we tear it down first and proceed.
        from shared.deploy import lifecycle
        probe = lifecycle.probe(target, mode=mode, host=host, ssh_key=ssh_key,
                                ssh_user=ssh_user, namespace=namespace)
        existing = probe.state in (
            lifecycle.ProbeState.DEPLOYED_RUNNING,
            lifecycle.ProbeState.DEPLOYED_STOPPED,
            lifecycle.ProbeState.DEPLOYED_UNREACHABLE,
        )
        if existing and force:
            # Tear down the existing deployment then continue with a fresh
            # deploy. Uses the same code path as ephemeral_lifecycle cleanup
            # (silent, idempotent — drops container + cache target row).
            from shared.deploy.lifecycle import _cleanup_ephemeral_deploy
            if not quiet:
                console = get_console()
                console.print(f"  [dim]--force: tearing down existing deployment for '{target}'...[/dim]")
            _cleanup_ephemeral_deploy(target, mode=mode)
        elif probe.state == lifecycle.ProbeState.DEPLOYED_RUNNING:
            return RdstResult(
                False,
                f"Cache is already deployed and running for '{target}'.\n"
                f"  • To recreate from scratch: rdst cache deploy --target {target} --mode {mode} --force\n"
                f"  • To restart the existing container: rdst cache restart --target {target}",
            )
        elif probe.state == lifecycle.ProbeState.DEPLOYED_STOPPED:
            return RdstResult(
                False,
                f"Cache container exists for '{target}' but is stopped.\n"
                f"  • To start it: rdst cache start --target {target}\n"
                f"  • To recreate from scratch: rdst cache deploy --target {target} --mode {mode} --force",
            )

        # Parse memory string ('4g', '512m', or raw bytes) → bytes
        memory_bytes = _parse_memory(memory) if memory else None

        input_data = CacheInput(target=target)
        options = CacheOptions(
            mode=mode, port=port, deploy_config=deploy_config,
            namespace=namespace, kubeconfig=kubeconfig,
            host=host, ssh_key=ssh_key, ssh_user=ssh_user,
            no_request_path=no_request_path,
            memory_bytes=memory_bytes,
            cpus=cpus,
        )
        success, data, error_msg = asyncio.run(
            self._execute_deploy(input_data, options, output_json, quiet=quiet)
        )
        if not success:
            return RdstResult(False, error_msg or "Deployment failed")
        return RdstResult(True, "")

    async def _execute_deploy(
        self, input_data: CacheInput, options: CacheOptions,
        output_json: bool, quiet: bool = False,
    ):
        console = get_console()
        service = CacheService()
        last_event = None

        if not quiet:
            console.print(f"\n{Icons.ROCKET} Deploying Readyset for target '{input_data.target}' ({options.mode})...\n")

        async for event in service.deploy(input_data, options):
            if isinstance(event, ProgressEvent):
                console.print(f"  {event.message}")
            last_event = event

        if isinstance(last_event, ErrorEvent):
            return (False, None, last_event.message)

        if isinstance(last_event, CacheDeployCompleteEvent):
            if quiet:
                return (True, None, None)
            elif output_json:
                print(json.dumps({
                    "success": True,
                    "endpoint": last_event.endpoint,
                    "cache_target": last_event.cache_target,
                    "container_name": last_event.container_name,
                }, indent=2))
            elif last_event.cache_target:
                container_line = (
                    f"  Container:    {last_event.container_name}\n\n"
                    if last_event.container_name
                    else "\n"
                )
                host_note = (
                    f"  Remote host:  {options.host}\n\n"
                    if options.host
                    else ""
                )
                console.print(StyledPanel(
                    f"Readyset deployed successfully\n\n"
                    f"  {Icons.SUCCESS} Connection endpoint:\n"
                    f"    {last_event.endpoint}\n\n"
                    f"{host_note}"
                    f"  Cache target: {last_event.cache_target}\n"
                    f"{container_line}"
                    f"  Next steps:\n"
                    f"    rdst query cache-compare <query> --target {input_data.target} --count 100\n"
                    f"    rdst cache add <query> --target {last_event.cache_target}\n"
                    f"    rdst cache show --target {last_event.cache_target}\n",
                    title="Deploy Complete",
                    variant="success",
                ))
            elif options.mode == "kubernetes":
                namespace = options.namespace or "readyset"
                port = self._endpoint_port(last_event.endpoint) or options.port or 5433
                service_name = f"readyset-cache-{input_data.target}"
                endpoint_block = (
                    f"  In-cluster endpoint:\n"
                    f"    {last_event.endpoint}\n\n"
                    if last_event.endpoint
                    else ""
                )
                console.print(StyledPanel(
                    f"Readyset deployed to Kubernetes\n\n"
                    f"{endpoint_block}"
                    f"  To use rdst cache commands from your machine, expose the service:\n"
                    f"    kubectl port-forward svc/{service_name} {port}:{port} -n {namespace}\n\n"
                    f"  Then register the cache target:\n"
                    f"    rdst configure add\n"
                    f"    host: 127.0.0.1\n"
                    f"    port: {port}\n",
                    title="Deploy Complete",
                    variant="success",
                ))
            else:
                console.print(StyledPanel(
                    f"Readyset deployed successfully via {options.mode}.\n\n"
                    f"  To use RDST with this deployment, add a cache target:\n"
                    f"    rdst configure add\n\n"
                    f"  You'll need the Readyset connection endpoint (host:port)\n"
                    f"  from your {options.mode} deployment.\n",
                    title="Deploy Complete",
                    variant="success",
                ))
            return (True, None, None)

        return (False, None, "Unexpected response")

    def _handle_script_only(
        self, target: str, mode: str, deploy_config: str,
        port: Optional[int], namespace: str, output_json: bool,
        no_request_path: bool = False,
    ) -> RdstResult:
        """Generate deploy script without executing."""
        from shared.config.targets import TargetsConfig
        from shared.deploy.script_generator import build_variables, generate_script

        config = TargetsConfig()
        config.load()
        target_config = config.get(target)
        if not target_config:
            return RdstResult(False, f"Target '{target}' not found.")

        password = resolve_password_value(target_config)
        variables = build_variables(
            target_name=target, target_config=target_config,
            password=password, port=port,
            deploy_config=deploy_config, namespace=namespace,
            no_request_path=no_request_path,
        )

        try:
            script = generate_script(mode, variables)
        except FileNotFoundError as e:
            return RdstResult(False, str(e))

        if output_json:
            print(json.dumps({
                "mode": mode, "script": script,
                "target": target, "deploy_config": deploy_config,
            }, indent=2))
        else:
            print(script)

        return RdstResult(True, "")

    def _build_endpoint(self, variables: dict, host: str = "localhost") -> str:
        """Build the Readyset connection endpoint string."""
        engine = variables["db_engine"]
        port = variables["readyset_port"]
        user = variables["db_user"]
        db = variables["db_name"]
        proto = "mysql" if engine == "mysql" else "postgresql"
        return f"{proto}://{user}:<password>@{host}:{port}/{db}"

    @staticmethod
    def _endpoint_port(endpoint: Optional[str]) -> Optional[int]:
        if not endpoint:
            return None
        parsed = urlparse(endpoint)
        return parsed.port
