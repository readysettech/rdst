from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import requests

from .models import BenchmarkCase

DATASET_REVISION = "f65faf4ae3b638c1fa6df1d3370c8d92c8366301"
GOLD_EXCLUSION_REVISION = "invalid-mysql-gold-v1"
MYSQL_INVALID_GOLD_CASE_IDS = (208, 212, 227, 327)
SCORABLE_CASE_COUNT = 500 - len(MYSQL_INVALID_GOLD_CASE_IDS)
PARTITION_REVISION = "bird-mini-dev-50-holdout-450-v1"
DEVELOPMENT_SIZE = 50
DEVELOPMENT_SEED = "rdst-bird-canary-v1"
PIPELINE_SMOKE_REVISION = "pipeline-failure-mechanisms-16-v1"
PIPELINE_SMOKE_CASE_IDS = (
    11,
    26,
    28,
    45,
    46,
    72,
    173,
    239,
    243,
    349,
    427,
    440,
    750,
    872,
    1141,
    1460,
)
_METADATA = {
    "mysql": (
        "mini_dev_mysql-00000-of-00001.json",
        "858aa16b97a05c8d6d8d448c081870c74958118ed22e2925a2feb0ee35c40920",
    ),
    "postgresql": (
        "mini_dev_pg-00000-of-00001.json",
        "7fa740ef9225389cff6c34432120e8325d0ca3008d73db1ae38731234bc10da7",
    ),
}
_METADATA_URL = (
    "https://huggingface.co/datasets/birdsql/bird_mini_dev/resolve/"
    f"{DATASET_REVISION}/data/{{filename}}"
)


class DatasetError(ValueError):
    pass


def default_cache_dir() -> Path:
    configured = os.getenv("RDST_BENCHMARK_CACHE")
    if configured:
        return Path(configured).expanduser() / "bird-mini"
    return Path.home() / ".cache" / "rdst" / "benchmarks" / "bird-mini"


def metadata_path(dialect: str, cache_dir: Path | None = None) -> Path:
    filename, _ = _metadata_info(dialect)
    return (cache_dir or default_cache_dir()) / "metadata" / filename


def download_metadata(
    dialect: str,
    *,
    cache_dir: Path | None = None,
    timeout_seconds: float = 60.0,
) -> Path:
    filename, expected_sha256 = _metadata_info(dialect)
    destination = metadata_path(dialect, cache_dir)
    if destination.exists() and sha256_file(destination) == expected_sha256:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(
        _METADATA_URL.format(filename=filename),
        stream=True,
        timeout=timeout_seconds,
    )
    response.raise_for_status()

    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{filename}.", suffix=".tmp"
    )
    try:
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "wb") as file_obj:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                digest.update(chunk)
                file_obj.write(chunk)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise DatasetError(
                f"Metadata hash mismatch for {filename}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        os.replace(temporary_name, destination)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return destination


def load_cases(path: Path, *, dialect: str) -> list[BenchmarkCase]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"Unable to load BIRD metadata {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise DatasetError("BIRD metadata must be a JSON array")

    cases = [
        _parse_case(item, dialect=dialect, index=index)
        for index, item in enumerate(raw)
    ]
    question_ids = [case.question_id for case in cases]
    if len(question_ids) != len(set(question_ids)):
        raise DatasetError("BIRD metadata contains duplicate question IDs")
    return cases


def load_cached_cases(
    dialect: str, *, cache_dir: Path | None = None
) -> list[BenchmarkCase]:
    path = metadata_path(dialect, cache_dir)
    _, expected_sha256 = _metadata_info(dialect)
    if not path.exists():
        raise DatasetError(
            f"BIRD metadata is missing at {path}; run the prepare command first"
        )
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise DatasetError(
            f"BIRD metadata hash mismatch at {path}: expected {expected_sha256}, "
            f"got {actual_sha256}"
        )
    return load_cases(path, dialect=dialect)


def select_canary(
    cases: Iterable[BenchmarkCase],
    *,
    size: int = DEVELOPMENT_SIZE,
    seed: str = DEVELOPMENT_SEED,
) -> list[BenchmarkCase]:
    case_list = list(cases)
    if size <= 0:
        raise ValueError("Canary size must be positive")
    if size > len(case_list):
        raise ValueError("Canary size exceeds the dataset")

    by_difficulty: dict[str, list[BenchmarkCase]] = defaultdict(list)
    for case in case_list:
        by_difficulty[case.difficulty].append(case)
    quotas = _proportional_quotas(by_difficulty, size, len(case_list))

    selected: list[BenchmarkCase] = []
    for difficulty in sorted(by_difficulty):
        by_database: dict[str, list[BenchmarkCase]] = defaultdict(list)
        for case in by_difficulty[difficulty]:
            by_database[case.db_id].append(case)
        for group in by_database.values():
            group.sort(key=lambda case: _selection_rank(case.question_id, seed))
        selected.extend(_round_robin_groups(by_database, quotas[difficulty]))

    if len(selected) != size:
        raise DatasetError(f"Selected {len(selected)} of {size} requested canary cases")
    return sorted(selected, key=lambda case: case.question_id)


def select_holdout(
    cases: Iterable[BenchmarkCase],
    *,
    development_size: int = DEVELOPMENT_SIZE,
    seed: str = DEVELOPMENT_SEED,
) -> list[BenchmarkCase]:
    """Return the immutable complement of the development partition."""
    case_list = list(cases)
    development_ids = {
        case.question_id
        for case in select_canary(case_list, size=development_size, seed=seed)
    }
    return sorted(
        (case for case in case_list if case.question_id not in development_ids),
        key=lambda case: case.question_id,
    )


def select_pipeline_smoke(cases: Iterable[BenchmarkCase]) -> list[BenchmarkCase]:
    """Select the preregistered 16-case diagnostic subset of development cases."""
    case_list = list(cases)
    by_id = {case.question_id: case for case in case_list}
    missing = sorted(set(PIPELINE_SMOKE_CASE_IDS) - set(by_id))
    if missing:
        raise DatasetError(
            "Pipeline smoke cases are missing from the dataset: "
            + ", ".join(map(str, missing))
        )
    development_ids = {case.question_id for case in select_canary(case_list)}
    outside = sorted(set(PIPELINE_SMOKE_CASE_IDS) - development_ids)
    if outside:
        raise DatasetError(
            "Pipeline smoke cases are outside the development partition: "
            + ", ".join(map(str, outside))
        )
    return [by_id[question_id] for question_id in PIPELINE_SMOKE_CASE_IDS]


def exclude_invalid_gold_cases(
    cases: Iterable[BenchmarkCase],
) -> list[BenchmarkCase]:
    """Exclude BIRD MySQL cases whose released reference SQL cannot execute."""
    return [
        case
        for case in cases
        if not (
            case.dialect == "mysql" and case.question_id in MYSQL_INVALID_GOLD_CASE_IDS
        )
    ]


def partition_provenance(cases: Iterable[BenchmarkCase]) -> dict[str, Any]:
    """Fingerprint the fixed development/holdout membership without question text."""
    case_list = list(cases)
    development = select_canary(case_list)
    holdout = select_holdout(case_list)

    def digest_ids(question_ids: Iterable[int]) -> str:
        encoded = json.dumps(list(question_ids), separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    return {
        "revision": PARTITION_REVISION,
        "seed": DEVELOPMENT_SEED,
        "development_size": len(development),
        "holdout_size": len(holdout),
        "development_case_ids_sha256": digest_ids(
            case.question_id for case in development
        ),
        "holdout_case_ids_sha256": digest_ids(case.question_id for case in holdout),
        "pipeline_smoke_revision": PIPELINE_SMOKE_REVISION,
        "pipeline_smoke_case_ids_sha256": digest_ids(PIPELINE_SMOKE_CASE_IDS),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metadata_info(dialect: str) -> tuple[str, str]:
    normalized = dialect.lower()
    if normalized == "postgres":
        normalized = "postgresql"
    try:
        return _METADATA[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(_METADATA))
        raise DatasetError(
            f"Unsupported BIRD dialect {dialect!r}; expected one of {supported}"
        ) from exc


def _parse_case(item: Any, *, dialect: str, index: int) -> BenchmarkCase:
    if not isinstance(item, dict):
        raise DatasetError(f"BIRD record {index} must be an object")
    required = ("question_id", "db_id", "question", "evidence", "SQL", "difficulty")
    missing = [key for key in required if key not in item]
    if missing:
        raise DatasetError(f"BIRD record {index} is missing: {', '.join(missing)}")
    if not isinstance(item["question_id"], int):
        raise DatasetError(f"BIRD record {index} has a non-integer question_id")
    for key in required[1:]:
        if not isinstance(item[key], str):
            raise DatasetError(f"BIRD record {index} has a non-string {key}")
    normalized_dialect = (
        "postgresql" if dialect.lower() == "postgres" else dialect.lower()
    )
    return BenchmarkCase(
        question_id=item["question_id"],
        db_id=item["db_id"],
        question=item["question"],
        evidence=item["evidence"],
        gold_sql=item["SQL"],
        difficulty=item["difficulty"],
        dialect=normalized_dialect,
    )


def _proportional_quotas(
    groups: dict[str, list[BenchmarkCase]], size: int, total: int
) -> dict[str, int]:
    exact = {key: size * len(group) / total for key, group in groups.items()}
    quotas = {key: int(value) for key, value in exact.items()}
    remaining = size - sum(quotas.values())
    order = sorted(groups, key=lambda key: (-(exact[key] - quotas[key]), key))
    for key in order[:remaining]:
        quotas[key] += 1
    return quotas


def _round_robin_groups(
    groups: dict[str, list[BenchmarkCase]], size: int
) -> list[BenchmarkCase]:
    selected = []
    round_index = 0
    keys = sorted(groups)
    while len(selected) < size:
        made_progress = False
        for key in keys:
            group = groups[key]
            if round_index >= len(group):
                continue
            selected.append(group[round_index])
            made_progress = True
            if len(selected) == size:
                break
        if not made_progress:
            break
        round_index += 1
    return selected


def _selection_rank(question_id: int, seed: str) -> bytes:
    return hashlib.sha256(f"{seed}:{question_id}".encode()).digest()
