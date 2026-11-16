"""Record the third-party components a PyInstaller-built sidecar ships.

Run with the build venv's interpreter after PyInstaller, so importlib.metadata sees the
distributions the build drew from:

    python sidecar-notices.py <onedir> <executable name> <project license file>

Writes <onedir>/third-party.json, which the desktop build renders into its notices file, and
puts the source of each LGPL distribution with compiled modules in <onedir>/third-party-source.
A distribution counts as shipped when one of its top-level modules is frozen into the
executable or collected into the onedir.
"""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import sys
import sysconfig
from importlib import metadata
from pathlib import Path, PurePosixPath
from urllib.request import urlopen

import certifi
from PyInstaller.archive.readers import CArchiveReader

RECORD_FILE = "third-party.json"
SOURCE_DIR = "third-party-source"
DOWNLOAD_TIMEOUT = 120
LICENSE_FILE = re.compile(r"^(licen[cs]e|copying|notice)([.-].*)?$", re.IGNORECASE)
NATIVE_SUFFIX = re.compile(r"\.(dll|pyd|dylib|so)(\.[0-9.]+)?$", re.IGNORECASE)
# Shared libraries, as opposed to Python extension modules (.pyd, *.cpython-312-*.so, *.abi3.so).
SHARED_LIBRARY = re.compile(
    r"^(?!.*\.(cpython-|abi3\.)).*\.(dll|dylib|so)(\.[0-9.]+)?$", re.IGNORECASE
)
PROJECT_DISTRIBUTION = "rdst"
# Licenses that oblige telling users where the source is. The LGPL also lets users replace the
# library, so an LGPL distribution's Python code must ship as files in the onedir, never sealed
# in the executable's archive (pyinstaller-hooks/ collects each as source), and the source of its
# compiled modules ships with the app.
SOURCE_LICENSE = re.compile(r"LGPL|Lesser General|MPL|Mozilla Public", re.IGNORECASE)
REPLACEABLE_LICENSE = re.compile(r"LGPL|Lesser General", re.IGNORECASE)
LICENSES_DIR = Path(__file__).resolve().parent / "licenses"
# psycopg2-binary's license refers to the LGPL-3.0, which builds on the GPL-3.0, without either
# text, and its source is the psycopg2 sdist.
DISTRIBUTION_EXTRAS = {
    "psycopg2-binary": {
        "license": "LGPL-3.0-or-later, with an OpenSSL linking exception",
        "sdist": "psycopg2",
        "texts": ["lgpl-3.0.txt", "gpl-3.0.txt"],
    },
}
# Native libraries whose licenses neither Python's LICENSE.txt nor the wheel that vendors them
# reproduces, matched by file name across the whole onedir.
NATIVE_LIBRARIES = [
    (
        re.compile(r"^lib(crypto|ssl)[-.]", re.IGNORECASE),
        {
            "name": "OpenSSL",
            "license": "Apache-2.0",
            "note": "Licensed under the Apache License 2.0, whose text is at the end of this file.",
        },
    ),
    (
        re.compile(r"^libffi[-.]", re.IGNORECASE),
        {"name": "libffi", "license": "MIT", "text": "libffi.txt"},
    ),
    (
        re.compile(r"^libpq[-.]", re.IGNORECASE),
        {
            "name": "libpq (PostgreSQL client library)",
            "license": "PostgreSQL",
            "text": "postgresql.txt",
        },
    ),
    (
        re.compile(r"^(lib)?sqlite3[-.]", re.IGNORECASE),
        {"name": "SQLite", "license": "Public domain", "note": "SQLite is in the public domain."},
    ),
    (
        re.compile(r"^(vcruntime|msvcp)\d", re.IGNORECASE),
        {
            "name": "Microsoft Visual C++ runtime",
            "license": "Microsoft Visual Studio redistributable code",
            "note": "Redistributed under the Microsoft Visual Studio license terms for redistributable code.",
        },
    ),
]


def frozen_modules(executable: Path) -> set[str]:
    archive = CArchiveReader(str(executable))
    names: set[str] = set()
    for name, (*_, typecode) in archive.toc.items():
        if typecode == "z":
            names.update(archive.open_embedded_archive(name).toc)
    return names


def collected_names(contents: Path) -> set[str]:
    """Top-level module names of the packages and extension modules in the onedir."""
    return {entry.name.split(".")[0] for entry in contents.iterdir()}


def declared_license(dist: metadata.Distribution) -> str:
    md = dist.metadata
    if md.get("License-Expression"):
        return md["License-Expression"]
    classifiers = [
        c.split(" :: ")[-1] for c in md.get_all("Classifier") or [] if c.startswith("License ::")
    ]
    if classifiers:
        return "; ".join(classifiers)
    first_line = (md.get("License") or "").strip().splitlines()[:1]
    return first_line[0] if first_line and len(first_line[0]) <= 80 else "See license text"


def license_texts(dist: metadata.Distribution) -> list[dict[str, str]]:
    files = sorted(
        (
            f
            for f in dist.files or ()
            if f.parts[0].endswith(".dist-info") and LICENSE_FILE.match(f.name)
        ),
        key=str,
    )
    texts = [
        {
            "file": "/".join(f.parts[1:]),
            "text": f.locate().read_text(encoding="utf-8", errors="replace"),
        }
        for f in files
    ]
    if texts:
        return texts
    # Older distributions put the whole license in the License field instead.
    field = (dist.metadata.get("License") or "").strip()
    if "\n" in field:
        return [{"file": None, "text": field + "\n"}]
    name = dist.metadata["Name"]
    raise SystemExit(f"{name} {dist.version} ships no license text")


def native_files(dist: metadata.Distribution) -> set[str]:
    return {str(PurePosixPath(*f.parts)) for f in dist.files or () if NATIVE_SUFFIX.search(f.name)}


def ship_source(name: str, version: str, target: Path) -> str:
    """Downloads a release's sdist from PyPI into target, checked against PyPI's digest."""
    context = ssl.create_default_context(cafile=certifi.where())
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urlopen(url, context=context, timeout=DOWNLOAD_TIMEOUT) as response:
        release = json.load(response)
    (sdist,) = [f for f in release["urls"] if f["packagetype"] == "sdist"]
    with urlopen(sdist["url"], context=context, timeout=DOWNLOAD_TIMEOUT) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != sdist["digests"]["sha256"]:
        raise SystemExit(f"{sdist['url']} does not match the sha256 PyPI lists for it")
    target.mkdir(exist_ok=True)
    (target / sdist["filename"]).write_bytes(data)
    return sdist["filename"]


def component(dist: metadata.Distribution, contents: Path) -> dict:
    name = dist.metadata["Name"]
    extras = DISTRIBUTION_EXTRAS.get(name.lower(), {})
    entry = {
        "name": name,
        "version": dist.version,
        "license": extras.get("license") or declared_license(dist),
    }
    source_name = extras.get("sdist", name)
    pypi = f"https://pypi.org/project/{source_name}/{dist.version}/#files"
    if REPLACEABLE_LICENSE.search(entry["license"]):
        if any((contents / path).exists() for path in native_files(dist)):
            archive = ship_source(source_name, dist.version, contents.parent / SOURCE_DIR)
            shipped = f"sidecar/rdst/{SOURCE_DIR}/{archive}"
        else:
            shipped = "its Python files in sidecar/rdst/_internal"
        entry["source"] = f"{shipped}, shipped with the app (also at {pypi})"
        entry["note"] = (
            "Ships as files in sidecar/rdst/_internal (Python source and any compiled modules), "
            "so it can be replaced with a modified version."
        )
    elif SOURCE_LICENSE.search(entry["license"]):
        entry["source"] = pypi
    entry["texts"] = license_texts(dist) + [
        {"file": text, "text": (LICENSES_DIR / text).read_text(encoding="utf-8")}
        for text in extras.get("texts", ())
    ]
    # Shared libraries a wheel vendors (numpy.libs, psycopg2_binary.libs) ship under its name.
    vendored = sorted(
        PurePosixPath(path).name
        for path in native_files(dist)
        if PurePosixPath(path).parts[0].endswith((".libs", ".dylibs"))
        and (contents / path).exists()
    )
    if vendored:
        entry["libraries"] = vendored
    return entry


def python_license() -> dict[str, str]:
    for candidate in (
        Path(sys.base_prefix, "LICENSE.txt"),
        Path(sysconfig.get_path("stdlib"), "LICENSE.txt"),
    ):
        if candidate.is_file():
            return {"file": "LICENSE.txt", "text": candidate.read_text(encoding="utf-8")}
    raise SystemExit(f"No LICENSE.txt in the Python installation at {sys.base_prefix}")


def main(onedir: Path, executable: str, project_license: Path) -> None:
    contents = onedir / "_internal"
    frozen = {name.split(".")[0] for name in frozen_modules(onedir / executable)}
    modules = frozen | collected_names(contents)

    owners = metadata.packages_distributions()
    names = {dist for module in modules for dist in owners.get(module, ())}
    dists = {}
    for name in names:
        dist = metadata.distribution(name)
        key = dist.metadata["Name"].lower()
        if key != PROJECT_DISTRIBUTION:
            dists[key] = dist
            sealed = sorted(module for module in frozen if name in owners.get(module, ()))
            if sealed and REPLACEABLE_LICENSE.search(declared_license(dist)):
                raise SystemExit(
                    f"{name} is under the LGPL, but {executable} seals {', '.join(sealed)} in its "
                    "archive; collect it as source with a hook in pyinstaller-hooks/"
                )
    components = [component(dists[key], contents) for key in sorted(dists)]

    # Every other shared library (not an extension module) came with the Python runtime or from
    # the build system, collected by PyInstaller.
    owned = set().union(*(native_files(dist) for dist in dists.values()))
    vendored = {name for entry in components for name in entry.get("libraries", ())}
    runtime_libraries = sorted(
        {
            path.name
            for path in contents.rglob("*")
            if path.is_file()
            and SHARED_LIBRARY.search(path.name)
            and path.name not in vendored
            and path.relative_to(contents).as_posix() not in owned
        }
    )
    components.append(
        {
            "name": "Python runtime",
            "version": sys.version.split()[0],
            "license": "PSF-2.0",
            "libraries": runtime_libraries,
            "note": "Includes the shared libraries PyInstaller collected with the Python runtime.",
            "texts": [python_license()],
        }
    )
    shipped_libraries = {
        path.name
        for path in contents.rglob("*")
        if path.is_file() and SHARED_LIBRARY.search(path.name)
    }
    for pattern, library in NATIVE_LIBRARIES:
        files = sorted(name for name in shipped_libraries if pattern.search(name))
        if not files:
            continue
        text = library.get("text")
        components.append(
            {
                "name": library["name"],
                "license": library["license"],
                "libraries": files,
                **({"note": library["note"]} if "note" in library else {}),
                "texts": [{"file": text, "text": (LICENSES_DIR / text).read_text(encoding="utf-8")}]
                if text
                else [],
            }
        )
    pyinstaller = metadata.distribution("pyinstaller")
    components.append(
        {
            "name": "PyInstaller bootloader",
            "version": pyinstaller.version,
            "license": "GPL-2.0-or-later WITH Bootloader-exception",
            "texts": license_texts(pyinstaller),
        }
    )

    project = {
        "name": "RDST",
        "license": "Apache License 2.0",
        "texts": [
            {"file": project_license.name, "text": project_license.read_text(encoding="utf-8")}
        ],
    }
    record = {"project": project, "components": components}
    (onedir / RECORD_FILE).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"[rdst-desktop] Recorded {len(components)} sidecar components in {onedir / RECORD_FILE}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
