"""RDST Deploy — shared Readyset deployment helpers."""

READYSET_IMAGE = "305232526136.dkr.ecr.us-east-2.amazonaws.com/readyset:latest"

from .kubernetes import deploy_kubernetes
from .local_docker import deploy_local_docker
from .local_systemd import deploy_local_systemd
from .remote import deploy_remote
from .script_generator import (
    SQUEEPY_IMAGE,
    build_variables,
    generate_k8s_apply_manifests,
    generate_script,
)

__all__ = [
    "READYSET_IMAGE",
    "SQUEEPY_IMAGE",
    "build_variables",
    "deploy_kubernetes",
    "deploy_local_docker",
    "deploy_local_systemd",
    "deploy_remote",
    "generate_k8s_apply_manifests",
    "generate_script",
]
