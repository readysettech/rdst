"""
LLM Inspector - Analyze LLM calls, prompts, and responses.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .formatters import Formatter as F

logger = logging.getLogger(__name__)


class LLMInspector:
    """Inspect LLM calls and analyze prompts/responses."""

    def __init__(self, session_file: Path):
        self.session_file = session_file
        self._session_data: Optional[Dict[str, Any]] = None

    def load_session(self) -> Dict[str, Any]:
        if self._session_data is None:
            with open(self.session_file, encoding="utf-8") as f:
                self._session_data = json.load(f)
        return self._session_data

    def list_calls(self) -> None:
        data = self.load_session()
        llm_calls = data.get("llm_calls", [])
        if not llm_calls:
            print(F.warning("No LLM calls found in this session"))
            return

        print(F.header("LLM CALLS"))
        print(F.metric("Total Calls", len(llm_calls)))

        total_tokens = sum(
            call.get("tokens_input", 0) + call.get("tokens_output", 0)
            for call in llm_calls
        )
        total_latency = sum(call.get("latency_ms", 0) for call in llm_calls)

        print(F.metric("Total Tokens", total_tokens))
        print(F.metric("Total Latency", F.format_duration(total_latency)))
        print()
        print(F.subheader("Call Timeline"))

        headers = ["#", "Call ID", "Time", "Function", "Model", "Tokens", "Latency", "Status"]
        rows = []
        for i, call in enumerate(llm_calls, 1):
            timestamp = call.get("timestamp", "")
            time_str = timestamp.split("T")[1].split(".")[0] if "T" in timestamp else ""
            function_name = call.get("function_name", "unknown")
            model = call.get("model", "unknown")
            tokens_in = call.get("tokens_input", 0)
            tokens_out = call.get("tokens_output", 0)
            total = tokens_in + tokens_out
            tokens_str = f"{total}" if total > 0 else "-"
            latency_ms = call.get("latency_ms", 0)
            latency_str = F.format_duration(latency_ms) if latency_ms > 0 else "-"
            success = call.get("success", True)
            status_str = f"{F.GREEN}✓{F.RESET}" if success else f"{F.RED}✗{F.RESET}"
            call_id_short = call.get("call_id", "")[:8]
            rows.append([
                str(i),
                call_id_short,
                time_str,
                function_name,
                model,
                tokens_str,
                latency_str,
                status_str,
            ])

        print(F.table(headers, rows))
        print(
            f"\n{F.DIM}Use 'rdstdbg llm SESSION_ID --call-id <#|ID>' to view details"
            f" (e.g., --call-id 1 or --call-id {call_id_short}){F.RESET}"
        )

    def show_call(self, call_id: Optional[str] = None, call_index: Optional[int] = None) -> None:
        data = self.load_session()
        llm_calls = data.get("llm_calls", [])
        if not llm_calls:
            print(F.warning("No LLM calls found"))
            return

        call = None
        if call_index is not None:
            if 1 <= call_index <= len(llm_calls):
                call = llm_calls[call_index - 1]
            else:
                print(F.error(f"Call index out of range: {call_index}"))
                return
        elif call_id:
            for candidate in llm_calls:
                if candidate.get("call_id", "").startswith(call_id):
                    call = candidate
                    break
            if not call:
                print(F.error(f"Call not found: {call_id}"))
                return
        else:
            call = llm_calls[0]

        print(F.header("LLM CALL DETAILS"))
        print(F.label("Call ID", call.get("call_id", "unknown")))
        print(F.label("Timestamp", call.get("timestamp", "unknown")))
        print(F.label("Function", call.get("function_name", "unknown")))
        print(F.label("Model", call.get("model", "unknown")))

        tokens_in = call.get("tokens_input", 0)
        tokens_out = call.get("tokens_output", 0)
        print(F.label("Tokens (in/out)", f"{tokens_in} / {tokens_out} (total: {tokens_in + tokens_out})"))

        latency_ms = call.get("latency_ms", 0)
        print(F.label("Latency", F.format_duration(latency_ms)))

        success = call.get("success", True)
        if success:
            print(F.success("Status: Success"))
        else:
            print(F.error(f"Status: Failed - {call.get('error', 'Unknown error')}"))

        metadata = call.get("metadata", {})
        if metadata:
            print(F.subheader("Metadata"))
            print(F.key_value_list(metadata, indent=1))

        prompt = call.get("prompt", "")
        if prompt:
            print(F.subheader("Prompt"))
            print(prompt)

        response = call.get("response", "")
        if response:
            print(F.subheader("Response"))
            print(response)

    def export_prompts(self, output_dir: Path) -> None:
        data = self.load_session()
        llm_calls = data.get("llm_calls", [])
        if not llm_calls:
            print(F.warning("No LLM calls to export"))
            return

        output_dir.mkdir(parents=True, exist_ok=True)
        print(F.header("EXPORTING LLM PROMPTS"))
        print(F.label("Output Directory", str(output_dir)))
        print(F.metric("LLM Calls", len(llm_calls)))
        print()

        for i, call in enumerate(llm_calls, 1):
            function_name = call.get("function_name", "unknown")
            call_id = call.get("call_id", "unknown")[:8]

            prompt_file = output_dir / f"{i:03d}_{function_name}_{call_id}_prompt.txt"
            with open(prompt_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(call.get("prompt", ""))

            response_file = output_dir / f"{i:03d}_{function_name}_{call_id}_response.txt"
            with open(response_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(call.get("response", ""))

            meta_file = output_dir / f"{i:03d}_{function_name}_{call_id}_meta.json"
            with open(meta_file, "w", encoding="utf-8", newline="\n") as f:
                json.dump(
                    {
                        "call_id": call.get("call_id"),
                        "timestamp": call.get("timestamp"),
                        "function_name": function_name,
                        "model": call.get("model"),
                        "tokens_input": call.get("tokens_input"),
                        "tokens_output": call.get("tokens_output"),
                        "latency_ms": call.get("latency_ms"),
                        "success": call.get("success"),
                        "metadata": call.get("metadata", {}),
                    },
                    f,
                    indent=2,
                )

            print(F.success(f"Exported call #{i}: {function_name}"))

        print(f"\n{F.success(f'Exported {len(llm_calls)} LLM calls to {output_dir}')}")
