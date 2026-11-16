"""Unit tests for schema-grounded example question generation.

Covers the deterministic paths (fingerprint, cache hit/miss, introspection
fallback). The LLM path is not exercised here — it needs a live provider.
Ports schema-grounded examples from CL 14059.
"""

from __future__ import annotations

import json

import features.ask.example_questions as eq


def test_introspection_examples_use_real_table_names():
    out = eq._introspection_examples(["title_ratings", "title_basics"])
    assert len(out) == 3
    assert any("title_ratings" in q for q in out)
    # No generic e-commerce placeholders.
    joined = " ".join(out).lower()
    assert "customer" not in joined and "order" not in joined


def test_no_examples_until_tables_are_known(monkeypatch, tmp_path):
    monkeypatch.setattr(eq, "rdst_data_dir", lambda: tmp_path)
    monkeypatch.setattr(eq, "has_anthropic_api_key", lambda: True)
    monkeypatch.setattr(
        eq, "_semantic_examples", lambda *a: (_ for _ in ()).throw(AssertionError())
    )
    assert eq.get_example_questions("t", [], object())["examples"] == []
    assert eq.get_example_questions("t", [], None)["examples"] == []


def test_generated_examples_ask_about_stored_data(monkeypatch):
    import shared.llm_manager as llm

    prompts = []

    class Manager:
        def query(self, **kwargs):
            prompts.append(kwargs["user_query"])
            return {"text": '{"questions": ["How many titles came out in 2021?"]}'}

    monkeypatch.setattr(llm, "LLMManager", Manager)
    layer = type("Layer", (), {"tables": {"title_basics": object()}})()
    assert eq._semantic_examples("t", layer) == ["How many titles came out in 2021?"]
    assert "not about the database's structure" in prompts[0]


def test_fingerprint_is_order_independent():
    assert eq._fingerprint(["a", "b"]) == eq._fingerprint(["b", "a"])
    assert eq._fingerprint(["a"]) != eq._fingerprint(["a", "b"])


def test_no_semantic_layer_falls_back_without_llm(monkeypatch, tmp_path):
    # Force cache into a temp dir so we never touch the real ~/.rdst.
    monkeypatch.setattr(eq, "rdst_data_dir", lambda: tmp_path)
    result = eq.get_example_questions("t", ["orders_raw", "line_items"], None)
    assert result["source"] == "introspection"
    assert any("orders_raw" in q for q in result["examples"])


def test_semantic_cache_hit_skips_generation(monkeypatch, tmp_path):
    monkeypatch.setattr(eq, "rdst_data_dir", lambda: tmp_path)
    tables = ["title_ratings"]
    cache = eq._cache_path("t")
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps({"fingerprint": eq._fingerprint(tables), "examples": ["cached q"]})
    )
    # Guard: if generation were attempted, this would raise.
    monkeypatch.setattr(
        eq, "_semantic_examples", lambda *a: (_ for _ in ()).throw(AssertionError())
    )
    monkeypatch.setattr(eq, "has_anthropic_api_key", lambda: True)
    result = eq.get_example_questions("t", tables, object())
    assert result == {"examples": ["cached q"], "source": "semantic"}


def test_stale_cache_fingerprint_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(eq, "rdst_data_dir", lambda: tmp_path)
    cache = eq._cache_path("t")
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"fingerprint": "old", "examples": ["stale"]}))
    # No key → falls through to introspection rather than the stale cache.
    monkeypatch.setattr(eq, "has_anthropic_api_key", lambda: False)
    result = eq.get_example_questions("t", ["new_table"], None)
    assert result["source"] == "introspection"
    assert "stale" not in result["examples"]
