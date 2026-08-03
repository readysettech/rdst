"""Regression tests for Windows locale-independent RDST text I/O."""

import builtins
import json
from pathlib import Path

from features.ask.debug.llm_inspector import LLMInspector


def test_llm_inspector_reads_and_exports_utf8_on_legacy_windows_locale(
    tmp_path: Path, monkeypatch
):
    session_file = tmp_path / "session.json"
    session_file.write_text(
        json.dumps(
            {
                "llm_calls": [
                    {
                        "call_id": "unicode-call",
                        "function_name": "résumé",
                        "prompt": "Explain café joins ☕",
                        "response": "数据库 ready ✅",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    real_open = builtins.open

    def legacy_windows_open(file, mode="r", *args, encoding=None, **kwargs):
        if "b" not in mode and encoding is None:
            encoding = "cp1252"
        return real_open(file, mode, *args, encoding=encoding, **kwargs)

    monkeypatch.setattr(builtins, "open", legacy_windows_open)

    output_dir = tmp_path / "export"
    LLMInspector(session_file).export_prompts(output_dir)

    assert next(output_dir.glob("*_prompt.txt")).read_text(encoding="utf-8") == (
        "Explain café joins ☕"
    )
    assert next(output_dir.glob("*_response.txt")).read_text(encoding="utf-8") == (
        "数据库 ready ✅"
    )
