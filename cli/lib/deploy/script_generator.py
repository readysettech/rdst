"""Generate deployment scripts from templates with variable injection.

Follows the same pattern as Cloud control plane observability agent
script generation (clusters.py lines 366-397): load a template file,
replace {variable} placeholders with actual values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict


TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

# Default ReadySet ports by engine
DEFAULT_PORTS = {
    "postgresql": 5433,
    "mysql": 3307,
}

# Default images
from lib.deploy import READYSET_IMAGE
SQUEEPY_IMAGE = "docker.io/readysettech/squeepy:latest"  # placeholder until published


def build_variables(
    target_name: str,
    target_config: dict,
    password: str,
    port: int | None = None,
    deploy_config: str = "readyset",
    namespace: str = "readyset",
) -> Dict[str, str]:
    """Build template variables dict from target config."""
    engine = target_config.get("engine", "postgresql")
    default_port = DEFAULT_PORTS.get(engine, 5433)
    readyset_port = port or default_port

    return {
        "db_host": target_config.get("host", "localhost"),
        "db_port": str(target_config.get("port", 5432)),
        "db_user": target_config.get("user", "postgres"),
        "db_name": target_config.get("database", ""),
        "db_engine": engine,
        "readyset_port": str(readyset_port),
        "container_name": f"rdst-readyset-{target_name}",
        "target_name": target_name,
        "readyset_image": READYSET_IMAGE,
        "squeepy_image": SQUEEPY_IMAGE,
        "deploy_config": deploy_config,
        "namespace": namespace,
    }


def generate_script(mode: str, variables: Dict[str, str]) -> str:
    """Load template and inject variables.

    Args:
        mode: "docker", "systemd", or "kubernetes"
        variables: Dict of {placeholder: value} pairs

    Returns:
        Rendered script/manifest content.
    """
    if mode == "kubernetes":
        return _generate_k8s_manifests(variables)

    template_file = TEMPLATE_DIR / f"deploy_{mode}.sh"
    if not template_file.exists():
        raise FileNotFoundError(f"Template not found: {template_file}")

    script = template_file.read_text()
    for key, value in variables.items():
        script = script.replace(f"{{{key}}}", str(value))
    return script


def generate_k8s_apply_manifests(variables: Dict[str, str]) -> str:
    """Generate only Deployment + Service manifests (Secret is handled via kubectl).

    Used by kubernetes.py when applying programmatically. Secret is created
    separately with the actual password via kubectl create secret.
    """
    return _generate_k8s_manifests(variables, include_secret=False)


def _generate_k8s_manifests(variables: Dict[str, str], include_secret: bool = True) -> str:
    """Combine K8s manifest templates into a single multi-document YAML."""
    template_names = ["deploy_k8s_deployment.yaml", "deploy_k8s_service.yaml"]
    if include_secret:
        template_names.insert(0, "deploy_k8s_secret.yaml")

    parts = []
    for name in template_names:
        template_file = TEMPLATE_DIR / name
        if not template_file.exists():
            raise FileNotFoundError(f"Template not found: {template_file}")
        content = template_file.read_text()
        for key, value in variables.items():
            content = content.replace(f"{{{key}}}", str(value))
        parts.append(content)

    return "---\n".join(parts)
