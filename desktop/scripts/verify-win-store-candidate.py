#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
from pathlib import Path


REQUIRED_CERTIFICATION_CHECKS = {
    "artifactManifest",
    "authenticode",
    "defender",
    "networkIsolation",
    "machineInstall",
    "launch",
    "privacyReset",
    "silentUninstall",
    "cleanup",
}


def find_one(root: Path, name: str) -> Path:
    matches = list(root.rglob(name))
    if len(matches) != 1:
        raise ValueError(f"expected one {name} under {root}; found {len(matches)}")
    return matches[0]


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read JSON evidence {path}: {error}") from error


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha512_base64(path: Path):
    digest = hashlib.sha512()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode("ascii")


def metadata_field(text: str, name: str):
    """A top-level `name: value` from electron-builder channel metadata."""
    for line in text.splitlines():
        if line.startswith(f"{name}:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


def metadata_files(text: str):
    """The `files:` entries of electron-builder channel metadata, the ones electron-updater
    downloads from, as dicts of their url, sha512 and size."""
    files, entry, inside = [], None, False
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "-")):
            inside = line.startswith("files:")
            continue
        if not inside:
            continue
        item = line.strip()
        if item.startswith("- "):
            entry = {}
            files.append(entry)
            item = item[2:]
        if entry is not None and ":" in item:
            key, value = item.split(":", 1)
            entry[key.strip()] = value.strip().strip("'\"")
    return files


def verify_build_artifacts(root: Path):
    manifest_path = find_one(root, "artifacts.json")
    artifact_root = manifest_path.parent.resolve()
    entries = load_json(manifest_path)
    if not isinstance(entries, list) or not entries:
        raise ValueError("Store artifact manifest must be a non-empty array")

    declared_files = set()
    for entry in entries:
        if not isinstance(entry, dict) or not {"path", "size", "sha256"} <= entry.keys():
            raise ValueError("Store artifact manifest contains an invalid entry")
        path = (artifact_root / entry["path"]).resolve()
        try:
            path.relative_to(artifact_root)
        except ValueError as error:
            raise ValueError(f"artifact path escapes its root: {entry['path']}") from error
        if not path.is_file():
            raise ValueError(f"artifact manifest references a missing file: {entry['path']}")
        if path.stat().st_size != entry["size"]:
            raise ValueError(f"artifact size mismatch: {entry['path']}")
        if sha256(path) != entry["sha256"]:
            raise ValueError(f"artifact hash mismatch: {entry['path']}")
        if entry["path"] in declared_files:
            raise ValueError(f"artifact manifest path is duplicated: {entry['path']}")
        declared_files.add(entry["path"])

    actual_files = {
        path.relative_to(artifact_root).as_posix()
        for path in artifact_root.rglob("*")
        if path.is_file()
    }
    expected_files = declared_files | {"artifacts.json", "SHA256SUMS"}
    if actual_files != expected_files:
        raise ValueError(
            "Store artifact manifest file set differs: "
            f"extra={sorted(actual_files - expected_files)} "
            f"missing={sorted(expected_files - actual_files)}"
        )

    installers = list(artifact_root.glob("*-store.exe"))
    if len(installers) != 1:
        raise ValueError(f"expected one Store installer; found {len(installers)}")
    installer = installers[0]
    installer_hash = sha256(installer)

    checksum_path = artifact_root / "SHA256SUMS"
    checksum_lines = [
        line for line in checksum_path.read_text(encoding="ascii").splitlines() if line
    ]
    if checksum_lines != [f"{installer_hash}  {installer.name}"]:
        raise ValueError("Store SHA256SUMS must contain only the exact installer hash")

    # The Store channel's metadata: one latest.yml naming exactly this installer, no blockmaps.
    updater_files = sorted(
        path.relative_to(artifact_root).as_posix()
        for path in artifact_root.rglob("*")
        if path.is_file()
        and (path.name.startswith("latest") and path.suffix == ".yml" or path.suffix == ".blockmap")
    )
    if updater_files != ["update/latest.yml"]:
        raise ValueError(f"Store artifacts must carry only update/latest.yml; found {updater_files}")
    metadata = (artifact_root / "update" / "latest.yml").read_text(encoding="utf-8")
    installer_sha512 = sha512_base64(installer)
    expected_file = {"url": installer.name, "sha512": installer_sha512,
                     "size": str(installer.stat().st_size)}
    files = metadata_files(metadata)
    if (
        metadata_field(metadata, "path") != installer.name
        or metadata_field(metadata, "sha512") != installer_sha512
        or not files
        or any({key: entry.get(key) for key in expected_file} != expected_file
               for entry in files)
    ):
        raise ValueError("Store latest.yml does not describe the Store installer")

    provenance = load_json(artifact_root / "evidence" / "provenance.json")
    required_build_checks = {
        "authenticode": "passed",
        "hostedReportUpload": "disabled",
        "inAppUpdates": "enabled",
    }
    if provenance.get("distribution") != "microsoft-store":
        raise ValueError("build provenance is not for the Microsoft Store distribution")
    if any(
        provenance.get("checks", {}).get(name) != expected
        for name, expected in required_build_checks.items()
    ):
        raise ValueError("build provenance does not contain the required Store checks")
    if not str(provenance["checks"].get("updateFeed", "")).endswith("/windows-store/update"):
        raise ValueError("Store build does not update from the Store channel")
    if metadata_field(metadata, "version") != str(provenance.get("version")):
        raise ValueError("Store latest.yml names a different version than the build")

    return artifact_root, installer, installer_hash, provenance


def verify_certification_evidence(certification_path: Path):
    evidence_root = certification_path.parent.resolve()
    manifest_path = evidence_root / "certification-evidence.json"
    entries = load_json(manifest_path)
    if not isinstance(entries, list) or not entries:
        raise ValueError("certification evidence manifest must be a non-empty array")

    required_files = {
        "certification.json",
        "defender-signature-update.log",
        "defender-installer.log",
        "defender-installed.log",
        "installed-authenticode.json",
        "network-isolation.json",
        "smoke.stdout.log",
        "smoke.stderr.log",
    }
    manifest_files = set()
    for entry in entries:
        if not isinstance(entry, dict) or not {"path", "size", "sha256"} <= entry.keys():
            raise ValueError("certification evidence manifest contains an invalid entry")
        path = (evidence_root / entry["path"]).resolve()
        try:
            path.relative_to(evidence_root)
        except ValueError as error:
            raise ValueError(
                f"certification evidence path escapes its root: {entry['path']}"
            ) from error
        if not path.is_file():
            raise ValueError(
                f"certification evidence manifest references a missing file: {entry['path']}"
            )
        if path.stat().st_size != entry["size"] or sha256(path) != entry["sha256"]:
            raise ValueError(f"certification evidence mismatch: {entry['path']}")
        if entry["path"] in manifest_files:
            raise ValueError(
                f"certification evidence path is duplicated: {entry['path']}"
            )
        manifest_files.add(entry["path"])

    missing = required_files - manifest_files
    if missing:
        raise ValueError(f"certification evidence is missing: {sorted(missing)}")
    actual_files = {
        path.relative_to(evidence_root).as_posix()
        for path in evidence_root.rglob("*")
        if path.is_file()
    }
    expected_files = manifest_files | {"certification-evidence.json"}
    if actual_files != expected_files:
        raise ValueError(
            "certification evidence manifest file set differs: "
            f"extra={sorted(actual_files - expected_files)} "
            f"missing={sorted(expected_files - actual_files)}"
        )


def verify_certification(root: Path, installer_hash: str, provenance):
    certification_path = find_one(root, "certification.json")
    verify_certification_evidence(certification_path)
    certification = load_json(certification_path)
    if certification.get("status") != "passed":
        raise ValueError("Store certification did not pass")
    if certification.get("installerSha256") != installer_hash:
        raise ValueError("Store certification references a different installer")
    if certification.get("version") != provenance.get("version"):
        raise ValueError("Store certification version differs from build provenance")
    expected_environment = {
        "isolation": "persistent_agent_rdst_state_checked",
        "globallyCleanMachine": False,
    }
    if certification.get("executionEnvironment") != expected_environment:
        raise ValueError("Store certification execution environment is not declared")

    checks = certification.get("checks")
    if not isinstance(checks, dict):
        raise ValueError("Store certification checks are missing")
    missing = REQUIRED_CERTIFICATION_CHECKS - checks.keys()
    if missing:
        raise ValueError(f"Store certification checks are missing: {sorted(missing)}")
    failed = {
        name: checks[name]
        for name in REQUIRED_CERTIFICATION_CHECKS
        if checks[name] != "passed"
    }
    if failed:
        raise ValueError(f"Store certification checks did not pass: {failed}")

    build_commit = provenance.get("buildkite", {}).get("commit")
    certification_commit = certification.get("buildkite", {}).get("commit")
    if not build_commit or certification_commit != build_commit:
        raise ValueError("Store build and certification commits do not match")
    expected_commit = os.environ.get("BUILDKITE_COMMIT")
    if expected_commit and build_commit != expected_commit:
        raise ValueError("Store evidence does not match the current Buildkite commit")

    return certification_path, certification


def create_evidence_bundle(
    artifact_root: Path, certification_path: Path, bundle_root: Path
):
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    build_root = bundle_root / "build"
    certification_root = bundle_root / "certification"
    build_root.mkdir(parents=True)
    shutil.copy2(artifact_root / "artifacts.json", build_root)
    shutil.copy2(artifact_root / "SHA256SUMS", build_root)
    shutil.copytree(artifact_root / "evidence", build_root / "evidence")
    shutil.copytree(certification_path.parent, certification_root)

    entries = []
    for path in sorted(bundle_root.rglob("*")):
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(bundle_root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    manifest_path = bundle_root / "evidence-manifest.json"
    manifest_path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    return manifest_path, sha256(manifest_path)


def verify_candidate(packages_root: Path, output: Path):
    artifact_root, installer, installer_hash, provenance = verify_build_artifacts(
        packages_root
    )
    certification_path, certification = verify_certification(
        packages_root, installer_hash, provenance
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    staged_installer = output.parent / installer.name
    shutil.copy2(installer, staged_installer)
    if sha256(staged_installer) != installer_hash:
        raise ValueError("staged Store installer hash differs from verified artifact")
    shutil.copy2(artifact_root / "update" / "latest.yml", output.parent / "latest.yml")
    evidence_manifest, evidence_manifest_hash = create_evidence_bundle(
        artifact_root, certification_path, output.parent / "evidence"
    )
    candidate = {
        "schemaVersion": 1,
        "status": "internal_validation_passed",
        "distribution": "microsoft-store",
        "validationScope": "pre-submission",
        "executionEnvironment": certification["executionEnvironment"],
        "partnerCenterPackageValidation": "required",
        "standardUserInstallValidation": "partner_center_required",
        "version": provenance["version"],
        "architecture": "x64",
        "installer": installer.name,
        "sha256": installer_hash,
        "silentInstallArguments": "/S",
        "installScope": "machine",
        "inAppUpdates": True,
        "updateFeed": provenance["checks"]["updateFeed"],
        "hostedReportUpload": False,
        "buildkite": provenance["buildkite"],
        "certificationJobId": certification["buildkite"]["jobId"],
        "evidenceManifest": evidence_manifest.relative_to(output.parent).as_posix(),
        "evidenceManifestSha256": evidence_manifest_hash,
    }
    output.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    return candidate


def main():
    parser = argparse.ArgumentParser(
        description="Verify an internally tested RDST Desktop Store candidate"
    )
    parser.add_argument("packages_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    verify_candidate(args.packages_root, args.output)


if __name__ == "__main__":
    main()
