"""RDST Deploy command — deploy ReadySet cache to local, remote, or Kubernetes."""

from __future__ import annotations

import json
import os
from typing import Optional

from lib.ui import get_console, StyledPanel, Icons

from .rdst_cli import RdstResult, TargetsConfig


class DeployCommand:
    """Deploy ReadySet shallow cache permanently."""

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
    ) -> RdstResult:
        if not target:
            return RdstResult(False, "Target is required. Use: rdst cache deploy --target <name> --mode <mode>")

        # Load target config
        target_config, password = self._load_target(target)
        if target_config is None:
            return RdstResult(False, password)  # password holds error message

        from lib.deploy.script_generator import build_variables

        variables = build_variables(
            target_name=target,
            target_config=target_config,
            password=password,
            port=port,
            deploy_config=deploy_config,
            namespace=namespace,
        )

        # Script-only mode: generate and print
        if script_only:
            return self._handle_script_only(mode, variables, output_json)

        # Route to deployer
        if mode == "kubernetes":
            result = self._deploy_kubernetes(target, variables, password, namespace, kubeconfig, output_json)
        elif host:
            result = self._deploy_remote(target, variables, password, mode, host, ssh_key, ssh_user, output_json)
        elif mode == "systemd":
            result = self._deploy_local_systemd(target, variables, password, output_json)
        else:
            result = self._deploy_local_docker(target, variables, password, output_json)

        # Auto-register ReadySet as a target on successful deploy
        if result.ok:
            console = get_console()
            port = variables["readyset_port"]
            cache_target = f"{target}-cache"

            if mode == "kubernetes":
                # K8s endpoint is internal-only — don't register, explain how to access
                console.print(StyledPanel(
                    f"ReadySet is running inside the cluster but is not directly\n"
                    f"accessible from your machine.\n\n"
                    f"  To use rdst cache commands against it, first expose the service:\n"
                    f"    kubectl port-forward svc/readyset-cache-{target} {port}:{port} -n {namespace}\n\n"
                    f"  Then register the target:\n"
                    f"    rdst configure add  (host: 127.0.0.1, port: {port})\n\n"
                    f"  Or from inside the cluster, use:\n"
                    f"    readyset-cache-{target}.{namespace}.svc.cluster.local:{port}",
                    title="Access",
                    variant="info",
                ))
            elif host:
                # Remote deploy — register but warn about port accessibility
                deploy_host = host
                new_target = self._register_readyset_target(target, target_config, variables, host=deploy_host)
                if new_target:
                    console.print(StyledPanel(
                        f"Registered new ReadySet target: {new_target}\n\n"
                        f"  Target type: readyset\n"
                        f"  Upstream:    {target}\n"
                        f"  Host:        {deploy_host}:{port}\n\n"
                        f"  {Icons.WARNING} Port {port} must be open on {deploy_host} for rdst to\n"
                        f"  reach this cache. If it is behind a firewall or security\n"
                        f"  group, you may need to allow inbound traffic on port {port}.\n\n"
                        f"  Next steps:\n"
                        f"    rdst cache add <query> --target {new_target}\n"
                        f"    rdst cache show --target {new_target}\n"
                        f"    rdst query run <hash> --target {new_target}",
                        title="New Target Added",
                        variant="info",
                    ))
            else:
                # Local deploy — register normally
                deploy_host = "127.0.0.1"
                new_target = self._register_readyset_target(target, target_config, variables, host=deploy_host)
                if new_target:
                    console.print(StyledPanel(
                        f"Registered new ReadySet target: {new_target}\n\n"
                        f"  Target type: readyset\n"
                        f"  Upstream:    {target}\n\n"
                        f"  Next steps:\n"
                        f"    rdst cache add <query> --target {new_target}\n"
                        f"    rdst cache show --target {new_target}\n"
                        f"    rdst query run <hash> --target {new_target}",
                        title="New Target Added",
                        variant="info",
                    ))

        return result

    def _load_target(self, target: str):
        """Load target config and resolve password. Returns (config, password) or (None, error_msg)."""
        try:
            config = TargetsConfig()
            config.load()
            target_config = config.get(target)
            if not target_config:
                available = ", ".join(config.list_targets()) if hasattr(config, "list_targets") else "none"
                return None, f"Target '{target}' not found. Available targets: {available}"

            password_env = target_config.get("password_env", "")
            password = os.environ.get(password_env, "") if password_env else ""

            return target_config, password
        except Exception as e:
            return None, f"Failed to load target config: {e}"

    def _handle_script_only(self, mode: str, variables: dict, output_json: bool) -> RdstResult:
        """Generate script and print to stdout."""
        from lib.deploy.script_generator import generate_script

        try:
            script = generate_script(mode, variables)
        except FileNotFoundError as e:
            return RdstResult(False, str(e))

        if output_json:
            print(json.dumps({
                "mode": mode,
                "script": script,
                "target": variables["target_name"],
                "deploy_config": variables["deploy_config"],
            }, indent=2))
        else:
            print(script)

        return RdstResult(True, "")

    def _build_endpoint(self, variables: dict, host: str = "localhost") -> str:
        """Build the ReadySet connection endpoint string."""
        engine = variables["db_engine"]
        port = variables["readyset_port"]
        user = variables["db_user"]
        db = variables["db_name"]
        proto = "mysql" if engine == "mysql" else "postgresql"
        return f"{proto}://{user}:<password>@{host}:{port}/{db}"

    def _register_readyset_target(
        self,
        original_target: str,
        target_config: dict,
        variables: dict,
        host: str = "localhost",
    ) -> Optional[str]:
        """Auto-register the deployed ReadySet instance as a new target.

        Returns the new target name if registered, None if already existed or failed.
        """
        readyset_target_name = f"{original_target}-cache"

        try:
            config = TargetsConfig()
            config.load()

            # Don't re-register if already exists
            if config.get(readyset_target_name):
                return None

            config.upsert(readyset_target_name, {
                "name": readyset_target_name,
                "target_type": "readyset",
                "engine": target_config.get("engine", "postgresql"),
                "host": host,
                "port": int(variables["readyset_port"]),
                "user": target_config.get("user", ""),
                "database": target_config.get("database", ""),
                "password_env": target_config.get("password_env", ""),
                "upstream_target": original_target,
                "container_name": variables["container_name"],
            })
            config.save()
            return readyset_target_name
        except Exception as e:
            # Best effort — don't fail deploy if registration fails
            try:
                console = get_console()
                console.print(f"\n  Warning: Could not auto-register target: {e}")
                console.print(f"  Register manually: rdst configure add\n")
            except Exception:
                pass
            return None

    def _deploy_local_docker(self, target: str, variables: dict, password: str, output_json: bool) -> RdstResult:
        """Deploy ReadySet locally via Docker."""
        from lib.deploy.local_docker import deploy_local_docker

        console = get_console()
        console.print(f"\n{Icons.ROCKET} Deploying ReadySet for target '{target}' (Docker)...\n")

        result = deploy_local_docker(target, variables, password)

        if result["success"]:
            container = result.get("container_name", "unknown")
            endpoint = self._build_endpoint(variables)
            already_running = result.get("already_running", False)

            if output_json:
                result["endpoint"] = endpoint
                print(json.dumps(result, indent=2))
            elif already_running:
                console.print(
                    StyledPanel(
                        f"ReadySet is already deployed and running\n\n"
                        f"  {Icons.SUCCESS} Connection endpoint:\n"
                        f"    {endpoint}\n\n"
                        f"  Container: {container}\n"
                        f"  Port:      {variables['readyset_port']}\n"
                        f"  Engine:    {variables['db_engine']}\n"
                        f"  Mode:      shallow cache\n\n"
                        f"  To redeploy with new settings, remove the container first:\n"
                        f"    docker rm -f {container}\n"
                        f"    rdst cache deploy --target {target} --mode <mode>",
                        title="Already Deployed",
                        variant="info",
                    )
                )
            else:
                status = "promoted" if result.get("promoted") else "created"
                console.print(
                    StyledPanel(
                        f"ReadySet deployed ({status})\n\n"
                        f"  {Icons.SUCCESS} Connection endpoint:\n"
                        f"    {endpoint}\n\n"
                        f"  Container: {container}\n"
                        f"  Port:      {variables['readyset_port']}\n"
                        f"  Engine:    {variables['db_engine']}\n"
                        f"  Mode:      shallow cache\n"
                        f"  Restart:   unless-stopped\n\n"
                        f"  Point your application to the endpoint above\n"
                        f"  instead of your database to use ReadySet.\n\n"
                        f"  Manage:\n"
                        f"    docker logs -f {container}\n"
                        f"    docker stop {container}\n"
                        f"    docker start {container}\n"
                        f"    docker rm -f {container}",
                        title="Deploy Complete",
                        variant="success",
                    )
                )
            return RdstResult(True, "")
        else:
            return RdstResult(False, result.get("error", "Deployment failed"))

    def _deploy_local_systemd(self, target: str, variables: dict, password: str, output_json: bool) -> RdstResult:
        """Deploy ReadySet locally as a systemd service."""
        from lib.deploy.local_systemd import deploy_local_systemd

        console = get_console()
        console.print(f"\n{Icons.ROCKET} Deploying ReadySet for target '{target}' (systemd)...\n")

        result = deploy_local_systemd(target, variables, password)

        if result["success"]:
            service_name = result.get("service_name", f"readyset-cache-{target}")
            endpoint = self._build_endpoint(variables)

            if output_json:
                result["endpoint"] = endpoint
                print(json.dumps(result, indent=2))
            else:
                console.print(
                    StyledPanel(
                        f"ReadySet deployed as systemd service\n\n"
                        f"  {Icons.SUCCESS} Connection endpoint:\n"
                        f"    {endpoint}\n\n"
                        f"  Service: {service_name}\n"
                        f"  Port:    {variables['readyset_port']}\n"
                        f"  Engine:  {variables['db_engine']}\n"
                        f"  Mode:    shallow cache\n\n"
                        f"  Point your application to the endpoint above\n"
                        f"  instead of your database to use ReadySet.\n\n"
                        f"  Manage:\n"
                        f"    sudo systemctl status {service_name}\n"
                        f"    sudo journalctl -u {service_name} -f\n"
                        f"    sudo systemctl restart {service_name}\n"
                        f"    sudo systemctl stop {service_name}",
                        title="Deploy Complete",
                        variant="success",
                    )
                )
            return RdstResult(True, "")
        else:
            return RdstResult(False, result.get("error", "Deployment failed"))

    def _deploy_kubernetes(
        self, target: str, variables: dict, password: str,
        namespace: str, kubeconfig: Optional[str], output_json: bool,
    ) -> RdstResult:
        """Deploy ReadySet to Kubernetes via kubectl."""
        from lib.deploy.kubernetes import deploy_kubernetes

        console = get_console()
        console.print(f"\n{Icons.ROCKET} Deploying ReadySet for target '{target}' (Kubernetes)...\n")

        result = deploy_kubernetes(target, variables, password, namespace, kubeconfig=kubeconfig)

        if result["success"]:
            svc = f"readyset-cache-{target}"
            endpoint = self._build_endpoint(
                variables,
                host=f"{svc}.{namespace}.svc.cluster.local",
            )

            if output_json:
                result["endpoint"] = endpoint
                print(json.dumps(result, indent=2))
            else:
                console.print(
                    StyledPanel(
                        f"ReadySet deployed to Kubernetes\n\n"
                        f"  {Icons.SUCCESS} Connection endpoint (in-cluster):\n"
                        f"    {endpoint}\n\n"
                        f"  Namespace:  {namespace}\n"
                        f"  Deployment: {svc}\n"
                        f"  Service:    {svc}\n"
                        f"  Port:       {variables['readyset_port']}\n"
                        f"  Mode:       shallow cache\n\n"
                        f"  Point your application to the endpoint above\n"
                        f"  instead of your database to use ReadySet.\n\n"
                        f"  Manage:\n"
                        f"    kubectl get pods -n {namespace}\n"
                        f"    kubectl logs -f deployment/{svc} -n {namespace}\n"
                        f"    kubectl delete deployment {svc} -n {namespace}",
                        title="Deploy Complete",
                        variant="success",
                    )
                )
            return RdstResult(True, "")
        else:
            return RdstResult(False, result.get("error", "Deployment failed"))

    def _deploy_remote(
        self, target: str, variables: dict, password: str,
        mode: str, host: str, ssh_key: Optional[str], ssh_user: str,
        output_json: bool,
    ) -> RdstResult:
        """Deploy ReadySet to a remote host via SSH."""
        from lib.deploy.remote import deploy_remote

        console = get_console()
        console.print(f"\n{Icons.ROCKET} Deploying ReadySet for target '{target}' to {ssh_user}@{host} ({mode})...\n")

        result = deploy_remote(
            target_name=target,
            variables=variables,
            password=password,
            mode=mode,
            host=host,
            ssh_key=ssh_key,
            ssh_user=ssh_user,
        )

        if result["success"]:
            endpoint = self._build_endpoint(variables, host=host)
            mgmt_script = result.get("management_script", "")

            if output_json:
                result["endpoint"] = endpoint
                print(json.dumps(result, indent=2))
            else:
                mgmt_section = ""
                if mgmt_script:
                    mgmt_section = (
                        f"\n  Management script (on {host}):\n"
                        f"    {mgmt_script} status\n"
                        f"    {mgmt_script} logs\n"
                        f"    {mgmt_script} restart\n"
                        f"    {mgmt_script} stop"
                    )
                    if mode == "systemd":
                        mgmt_section += f"\n    {mgmt_script} uninstall"

                console.print(
                    StyledPanel(
                        f"ReadySet deployed to {host}\n\n"
                        f"  {Icons.SUCCESS} Connection endpoint:\n"
                        f"    {endpoint}\n\n"
                        f"  Host:   {host}\n"
                        f"  Mode:   {mode}\n"
                        f"  Port:   {variables['readyset_port']}\n"
                        f"  Engine: {variables['db_engine']}\n"
                        f"  Cache:  shallow"
                        f"{mgmt_section}\n\n"
                        f"  Point your application to the endpoint above\n"
                        f"  instead of your database to use ReadySet.",
                        title="Deploy Complete",
                        variant="success",
                    )
                )
            return RdstResult(True, "")
        else:
            return RdstResult(False, result.get("error", "Remote deployment failed"))
