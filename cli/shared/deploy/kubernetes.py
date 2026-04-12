"""Kubernetes deployment — generate manifests and apply via kubectl.

Creates namespace, Secret (with password), Deployment, and Service.
"""

from __future__ import annotations

import subprocess  # nosec B404  # nosemgrep: gitlab.bandit.B404
from typing import Any, Dict, Optional
from urllib.parse import quote as urlquote

from .script_generator import generate_k8s_apply_manifests


def _kubectl_cmd(args: list[str], kubeconfig: Optional[str] = None) -> list[str]:
    """Build a kubectl command, prepending --kubeconfig when provided."""
    cmd = ["kubectl"]
    if kubeconfig:
        cmd.extend(["--kubeconfig", kubeconfig])
    cmd.extend(args)
    return cmd


def deploy_kubernetes(
    target_name: str,
    variables: Dict[str, str],
    password: str,
    namespace: str = "readyset",
    kubeconfig: Optional[str] = None,
) -> Dict[str, Any]:
    """Deploy ReadySet to Kubernetes via kubectl.

    Steps:
    1. Check kubectl is available
    2. Create namespace (idempotent)
    3. Create Secret with actual password
    4. Apply Deployment + Service manifests
    5. Wait for rollout
    """
    # Check kubectl is available
    if not _check_kubectl():
        return {
            "success": False,
            "error": (
                "kubectl is not installed or not in PATH.\n"
                "Install kubectl: https://kubernetes.io/docs/tasks/tools/"
            ),
        }

    # Validate kubeconfig file if provided
    if kubeconfig:
        import os
        if not os.path.isfile(kubeconfig):
            return {
                "success": False,
                "error": f"Kubeconfig file not found: {kubeconfig}",
            }

    # Check cluster connectivity
    cluster_err = _check_cluster(kubeconfig)
    if cluster_err:
        return {"success": False, "error": cluster_err}

    # 1. Create namespace
    ns_result = _create_namespace(namespace, kubeconfig)
    if not ns_result["success"]:
        return ns_result

    # 2. Create secret with actual password
    secret_result = _create_secret(target_name, variables, password, namespace, kubeconfig)
    if not secret_result["success"]:
        return secret_result

    # 3. Generate and apply manifests (Deployment + Service only, Secret handled above)
    apply_result = _apply_manifests(target_name, variables, namespace, kubeconfig)
    if not apply_result["success"]:
        return apply_result

    # 4. Wait for rollout
    rollout_result = _wait_for_rollout(target_name, namespace, kubeconfig)

    return {
        "success": True,
        "namespace": namespace,
        "deployment": f"readyset-cache-{target_name}",
        "service": f"readyset-cache-{target_name}",
        "port": variables["readyset_port"],
        "rollout_ready": rollout_result.get("ready", False),
    }


def _check_kubectl() -> bool:
    """Check if kubectl is available."""
    try:
        result = subprocess.run(
            ["kubectl", "version", "--client", "--output=json"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_cluster(kubeconfig: Optional[str] = None) -> Optional[str]:
    """Check if we can reach the Kubernetes cluster. Returns error message or None on success."""
    try:
        result = subprocess.run(
            _kubectl_cmd(["cluster-info"], kubeconfig),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return None
        stderr = result.stderr.strip()
        if kubeconfig:
            return (
                f"Cannot connect to Kubernetes cluster using kubeconfig: {kubeconfig}\n"
                f"{stderr}\n\n"
                f"Verify the file is valid: kubectl --kubeconfig {kubeconfig} cluster-info"
            )
        return (
            f"Cannot connect to Kubernetes cluster.\n"
            f"{stderr}\n\n"
            f"Check your kubeconfig or pass one explicitly: --kubeconfig /path/to/kubeconfig.yaml"
        )
    except FileNotFoundError:
        return "kubectl not found."
    except subprocess.TimeoutExpired:
        return "Timed out connecting to Kubernetes cluster."


def _create_namespace(namespace: str, kubeconfig: Optional[str] = None) -> Dict[str, Any]:
    """Create namespace if it doesn't exist (idempotent)."""
    try:
        result = subprocess.run(
            _kubectl_cmd(
                ["create", "namespace", namespace, "--dry-run=client", "-o", "yaml"],
                kubeconfig,
            ),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"success": False, "error": f"Failed to generate namespace YAML: {result.stderr.strip()}"}

        apply_result = subprocess.run(
            _kubectl_cmd(["apply", "-f", "-"], kubeconfig),
            input=result.stdout,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if apply_result.returncode != 0:
            return {"success": False, "error": f"Failed to create namespace '{namespace}': {apply_result.stderr.strip()}"}

        return {"success": True}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timed out creating namespace."}


def _create_secret(
    target_name: str,
    variables: Dict[str, str],
    password: str,
    namespace: str,
    kubeconfig: Optional[str] = None,
) -> Dict[str, Any]:
    """Create Kubernetes Secret with the database URL (including password)."""
    engine = variables["db_engine"]
    db_user = variables["db_user"]
    db_host = variables["db_host"]
    db_port = variables["db_port"]
    db_name = variables["db_name"]

    safe_user = urlquote(db_user, safe="")
    safe_password = urlquote(password, safe="")
    if engine == "mysql":
        db_url = f"mysql://{safe_user}:{safe_password}@{db_host}:{db_port}/{db_name}"
    else:
        db_url = f"postgresql://{safe_user}:{safe_password}@{db_host}:{db_port}/{db_name}"

    secret_name = f"readyset-{target_name}"

    try:
        # Use --dry-run + apply for idempotency
        gen_result = subprocess.run(
            _kubectl_cmd(
                [
                    "create", "secret", "generic", secret_name,
                    f"--from-literal=database-url={db_url}",
                    "-n", namespace,
                    "--dry-run=client", "-o", "yaml",
                ],
                kubeconfig,
            ),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if gen_result.returncode != 0:
            return {"success": False, "error": f"Failed to generate secret: {gen_result.stderr.strip()}"}

        apply_result = subprocess.run(
            _kubectl_cmd(["apply", "-f", "-"], kubeconfig),
            input=gen_result.stdout,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if apply_result.returncode != 0:
            return {"success": False, "error": f"Failed to create secret: {apply_result.stderr.strip()}"}

        return {"success": True}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timed out creating secret."}


def _apply_manifests(
    target_name: str,
    variables: Dict[str, str],
    namespace: str,
    kubeconfig: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate and apply Deployment + Service manifests."""
    try:
        manifests = generate_k8s_apply_manifests(variables)
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}

    try:
        result = subprocess.run(
            _kubectl_cmd(["apply", "-f", "-"], kubeconfig),
            input=manifests,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {"success": False, "error": f"Failed to apply manifests: {result.stderr.strip()}"}

        if result.stdout:
            print(result.stdout)

        return {"success": True}

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timed out applying manifests."}


def _wait_for_rollout(
    target_name: str,
    namespace: str,
    kubeconfig: Optional[str] = None,
) -> Dict[str, Any]:
    """Wait briefly for the deployment rollout, then return status."""
    deployment_name = f"readyset-cache-{target_name}"

    try:
        print(f"Waiting for deployment '{deployment_name}' to be ready...")
        result = subprocess.run(
            _kubectl_cmd(
                [
                    "rollout", "status",
                    f"deployment/{deployment_name}",
                    "-n", namespace,
                    "--timeout=15s",
                ],
                kubeconfig,
            ),
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.stdout:
            print(result.stdout.strip())

        if result.returncode != 0:
            # Check if pod is crash-looping — give actionable feedback
            pod_result = subprocess.run(
                _kubectl_cmd(
                    ["get", "pods", "-n", namespace, "-l", f"app=readyset-cache-{target_name}",
                     "-o", "jsonpath={.items[0].status.containerStatuses[0].state}"],
                    kubeconfig,
                ),
                capture_output=True,
                text=True,
                timeout=10,
            )
            pod_status = pod_result.stdout if pod_result.returncode == 0 else ""
            if "CrashLoopBackOff" in pod_status or "Error" in pod_status:
                print(f"\nPod is crash-looping. Check logs:")
                print(f"  kubectl logs deployment/{deployment_name} -n {namespace}")
                print(f"  (Common cause: ReadySet can't reach the upstream database from inside the cluster)")

        return {"ready": result.returncode == 0}

    except subprocess.TimeoutExpired:
        print("Rollout not ready yet — pod may still be starting.")
        print(f"  kubectl get pods -n {namespace}")
        print(f"  kubectl logs deployment/{deployment_name} -n {namespace}")
        return {"ready": False}
