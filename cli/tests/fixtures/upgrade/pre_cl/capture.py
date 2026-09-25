"""Finish and archive data produced by the pre-CL CLI on disposable demo databases.

Run with the current environment's Python after sourcing e2e.env:
  python capture.py --base /path/to/pre-cl/rdst --data-dir /fresh/home/.rdst

Before capture, use that base's CLI to query add, analyze --fast --json, top
--duration 3 --json, and schema init --force for both targets. Point targets at
localhost, with password_env credentials. The base predates RDST_DATA_DIR, so
its CLI launcher redirects Path.home to the disposable data directory's parent.
The manifest records the resulting entries and analyses independently of current
normalization code. This script imports only the base's storage APIs.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import sqlite3
import sys
import tarfile
import time
from pathlib import Path

import toml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    base, data = args.base.resolve(), args.data_dir.resolve()
    sys.path.insert(0, str(base))
    os.environ["RDST_DATA_DIR"] = str(data)
    Path.home = classmethod(lambda cls: data.parent)
    from _version import MAJOR, MINOR
    from features.schema.semantic_layer.manager import SemanticLayerManager
    from shared.query_registry.query_registry import QueryRegistry
    from shared.query_registry.observation_store import ObservationStore
    from shared.query_registry.conversation_registry import ConversationRegistry
    from shared.llm_manager import LLMManager

    registry = QueryRegistry()
    registry.load()
    # Old identities with different value-list arities collide in library v16.
    for sql in ("SELECT id FROM orders WHERE id IN (1, 2)",
                "SELECT id FROM orders WHERE id IN (1, 2, 3, 4)"):
        registry.add_query(sql, source="top", target="upgrade-pg", observed=True,
                           observed_params_sql=sql, dialect="postgres")
    entries = registry.list_queries()
    with ObservationStore() as cache:
        now = int(time.time())
        for index, entry in enumerate(entries):
            for target in entry.target_lifecycle:
                cache.record_identity_aliases(target, "pre-cl-epoch", {
                    f"engine-{index}": entry.hash})
                cache.record_recent_observations(target, [
                    {"normalized_hash": entry.hash, "window_start": now - 60 * (minute + 1),
                     "window_end": now - 60 * minute, "calls_delta": (index + 1) * 7,
                     "exec_time_delta": (index + 1) * 1.5, "approximate_qps": 0.1,
                     "completeness": "complete"} for minute in range(12)])
    manager = SemanticLayerManager()
    for target in ("upgrade-pg", "upgrade-mysql"):
        assert manager.exists(target), "Run the old schema init first"
        manager.add_table(target, "orders", "Orders placed in the demo shop")
        manager.add_column(target, "orders", "id", "Stable order identifier")
        manager.add_terminology(target, "orders", "Orders placed by customers",
                                "SELECT count(*) FROM orders", tables_used=["orders"])
    # Persist local review history through the old conversation API.
    entry = registry.get_query_by_tag("pg-order-count")
    analysis = registry.library_store.analysis_payloads(entry.hash)[0]
    conversations = ConversationRegistry()
    defaults = LLMManager().defaults
    conversation = conversations.create_conversation(
        entry.hash, defaults.provider, defaults.model, analysis["analysis_id"],
        "upgrade-pg", entry.original_sql)
    conversation.add_exchange("Show the captured execution plan", json.dumps(analysis["explain_plan"]))
    conversations.save_conversation(conversation)
    registry.export_toml_projection(str(data / "queries.toml"))
    config = toml.load(data / "config.toml")
    for target in config["targets"].values():
        assert target["host"] == "localhost"
        assert "password" not in target and target["password_env"]
    config["targets"]["upgrade-pg"]["password_env"] = "PRECL_PG_PASSWORD"
    (data / "config.toml").write_text(toml.dumps(config))
    assert registry.library_store is not None
    manifest = {"base_commit": "c44c96db91972b9135718740e2392ddab05e0638",
                "source_version": f"{MAJOR}.{MINOR}", "source_kind": "merge-base source CLI",
                "library_schema": 15, "cache_schema": 2,
                "commands": ["query add -q SQL --target TARGET",
                             "analyze --name NAME --target TARGET --fast --json --skip-warning",
                             "top --target TARGET --duration 3 --json",
                             "schema init --target TARGET --force", "query export"],
                "home_redirect": "Path.home returns the fresh RDST_DATA_DIR parent",
                "supplemental_apis": ["ObservationStore.record_recent_observations",
                    "ObservationStore.record_identity_aliases", "SemanticLayerManager.add_table",
                    "SemanticLayerManager.add_column", "SemanticLayerManager.add_terminology",
                    "ConversationRegistry.save_conversation"],
                "config": config, "queries": [entry.to_dict() for entry in entries],
                "analyses": {entry.hash: registry.library_store.analysis_payloads(entry.hash)
                             for entry in entries}, "source_sha256": {
                    name: hashlib.sha256((base / name).read_bytes()).hexdigest()
                    for name in ("rdst.py", "shared/query_registry/library_store.py",
                                 "shared/query_registry/query_registry.py")}}
    assert sum(len(v) for v in manifest["analyses"].values()) >= 2
    with sqlite3.connect(data / "cache.db") as conn:
        conn.row_factory = sqlite3.Row
        manifest["aliases"] = [dict(r) for r in conn.execute(
            "SELECT * FROM identity_alias ORDER BY target_id,engine_key")]
        manifest["observations"] = [dict(r) for r in conn.execute(
            "SELECT * FROM recent_observation ORDER BY target_id,normalized_hash,window_end")]
    files = [data / "config.toml", data / "queries.toml", data / "library.db", data / "cache.db",
             *sorted((data / "semantic-layer").glob("*.yaml")),
             *sorted((data / "conversations").glob("*.json"))]
    for path in files:
        if path.suffix == ".db":
            with sqlite3.connect(path) as conn:
                assert conn.execute("PRAGMA quick_check").fetchone() == ("ok",)
                conn.execute("VACUUM")
    manifest["files"] = {str(p.relative_to(data)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in files}
    manifest["semantic_layers"] = {p.stem: p.read_text() for p in files if p.suffix == ".yaml"}
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for path in files:
            info = tarfile.TarInfo(str(path.relative_to(data)))
            content = path.read_bytes()
            info.size, info.mtime, info.mode = len(content), 0, 0o600
            tar.addfile(info, io.BytesIO(content))
    output = Path(__file__).resolve().parent
    packed = gzip.compress(archive.getvalue(), mtime=0)
    (output / "data.tar.gz").write_bytes(packed)
    manifest["archive_sha256"] = hashlib.sha256(packed).hexdigest()
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"Captured {len(entries)} queries and {sum(map(len, manifest['analyses'].values()))} analyses")


if __name__ == "__main__":
    main()
