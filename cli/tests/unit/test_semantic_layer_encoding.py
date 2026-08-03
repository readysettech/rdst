"""Encoding regressions for semantic-layer persistence."""

from __future__ import annotations

import builtins

from features.schema.semantic_models import SemanticLayer


def test_semantic_layer_unicode_roundtrip_on_legacy_windows_locale(
    tmp_path, monkeypatch
):
    real_open = builtins.open

    def windows_open(file, mode="r", *args, **kwargs):
        if "b" not in mode and "encoding" not in kwargs:
            kwargs["encoding"] = "cp1252"
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", windows_open)
    path = tmp_path / "semantic-layer.yaml"
    SemanticLayer(target="prod", notes="東京 ▶").save(path)

    assert SemanticLayer.load(path).notes == "東京 ▶"
    assert "東京" in path.read_text(encoding="utf-8")
