"""
LLM Inspector - Analyze LLM calls, prompts, and responses.

Usage:
    rdstdbg llm SESSION_ID
    rdstdbg llm SESSION_ID --call-id CALL_ID
    rdstdbg llm SESSION_ID --export-prompts DIR
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from .formatters import Formatter as F

logger = logging.getLogger(__name__)


class LLMInspector:
    """Inspect LLM calls and analyze prompts/responses."""

    def __init__(self, session_file: Path):
        """
        Initialize LLM inspector.

        Args:
            session_file: Path to session.json file
        """
        self.session_file = session_file
        self._session_data: Optional[Dict[str, Any]] = None

    def load_session(self) -> Dict[str, Any]:
        """Load session data."""
        if self._session_data is None:
            with open(self.session_file) as f:
                self._session_data = json.load(f)
        return self._session_data

    def list_calls(self) -> None:
        """List all LLM calls with summary information."""
        data = self.load_session()
        llm_calls = data.get('llm_calls', [])

        if not llm_calls:
            print(F.warning("No LLM calls found in this session"))
            return

        print(F.header("LLM CALLS"))
        print(F.metric("Total Calls", len(llm_calls)))

        # Calculate totals
        total_tokens = sum(call.get('tokens_input', 0) + call.get('tokens_output', 0) for call in llm_calls)
        total_latency = sum(call.get('latency_ms', 0) for call in llm_calls)

        print(F.metric("Total Tokens", total_tokens))
        print(F.metric("Total Latency", F.format_duration(total_latency)))
        print()

        # Print table of calls
        print(F.subheader("Call Timeline"))

        headers = ["#", "Call ID", "Time", "Function", "Model", "Tokens", "Latency", "Status"]
        rows = []

        for i, call in enumerate(llm_calls, 1):
            timestamp = call.get('timestamp', '')
            time_str = timestamp.split('T')[1].split('.')[0] if 'T' in timestamp else ''

            function_name = call.get('function_name', 'unknown')
            model = call.get('model', 'unknown')

            tokens_in = call.get('tokens_input', 0)
            tokens_out = call.get('tokens_output', 0)
            total = tokens_in + tokens_out
            tokens_str = f"{total}" if total > 0 else "-"

            latency_ms = call.get('latency_ms', 0)
            latency_str = F.format_duration(latency_ms) if latency_ms > 0 else "-"

            success = call.get('success', True)
            status_str = f"{F.GREEN}✓{F.RESET}" if success else f"{F.RED}✗{F.RESET}"

            call_id_short = call.get('call_id', '')[:8]

            rows.append([
                str(i),
                call_id_short,
                time_str,
                function_name,
                model,
                tokens_str,
                latency_str,
                status_str
            ])

        print(F.table(headers, rows))

        print(f"\n{F.DIM}Use 'rdstdbg llm SESSION_ID --call-id <#|ID>' to view details (e.g., --call-id 1 or --call-id {call_id_short}){F.RESET}")

    def show_call(self, call_id: Optional[str] = None, call_index: Optional[int] = None) -> None:
        """
        Show detailed information about a specific LLM call.

        Args:
            call_id: Full or partial call ID
            call_index: Call index (1-based)
        """
        data = self.load_session()
        llm_calls = data.get('llm_calls', [])

        if not llm_calls:
            print(F.warning("No LLM calls found"))
            return

        # Find the call
        call = None
        if call_index is not None:
            if 1 <= call_index <= len(llm_calls):
                call = llm_calls[call_index - 1]
            else:
                print(F.error(f"Call index out of range: {call_index}"))
                return
        elif call_id:
            for c in llm_calls:
                if c.get('call_id', '').startswith(call_id):
                    call = c
                    break
            if not call:
                print(F.error(f"Call not found: {call_id}"))
                return
        else:
            # Show first call if no ID/index specified
            call = llm_calls[0]

        # Display call details
        print(F.header("LLM CALL DETAILS"))

        print(F.label("Call ID", call.get('call_id', 'unknown')))
        print(F.label("Timestamp", call.get('timestamp', 'unknown')))
        print(F.label("Function", call.get('function_name', 'unknown')))
        print(F.label("Model", call.get('model', 'unknown')))

        # Tokens
        tokens_in = call.get('tokens_input', 0)
        tokens_out = call.get('tokens_output', 0)
        print(F.label("Tokens (in/out)", f"{tokens_in} / {tokens_out} (total: {tokens_in + tokens_out})"))

        # Latency
        latency_ms = call.get('latency_ms', 0)
        print(F.label("Latency", F.format_duration(latency_ms)))

        # Status
        success = call.get('success', True)
        if success:
            print(F.success("Status: Success"))
        else:
            print(F.error(f"Status: Failed - {call.get('error', 'Unknown error')}"))

        # Metadata
        metadata = call.get('metadata', {})
        if metadata:
            print(F.subheader("Metadata"))
            print(F.key_value_list(metadata, indent=1))

        # Prompt
        prompt = call.get('prompt', '')
        if prompt:
            print(F.subheader("Prompt"))
            print(prompt)

        # Response
        response = call.get('response', '')
        if response:
            print(F.subheader("Response"))
            print(response)

    def export_prompts(self, output_dir: Path) -> None:
        """
        Export all prompts to text files.

        Args:
            output_dir: Directory to write prompt files
        """
        data = self.load_session()
        llm_calls = data.get('llm_calls', [])

        if not llm_calls:
            print(F.warning("No LLM calls to export"))
            return

        output_dir.mkdir(parents=True, exist_ok=True)

        print(F.header("EXPORTING LLM PROMPTS"))
        print(F.label("Output Directory", str(output_dir)))
        print(F.metric("LLM Calls", len(llm_calls)))
        print()

        for i, call in enumerate(llm_calls, 1):
            function_name = call.get('function_name', 'unknown')
            call_id = call.get('call_id', 'unknown')[:8]

            # Write prompt
            prompt_file = output_dir / f"{i:03d}_{function_name}_{call_id}_prompt.txt"
            with open(prompt_file, 'w') as f:
                f.write(call.get('prompt', ''))

            # Write response
            response_file = output_dir / f"{i:03d}_{function_name}_{call_id}_response.txt"
            with open(response_file, 'w') as f:
                f.write(call.get('response', ''))

            # Write metadata
            meta_file = output_dir / f"{i:03d}_{function_name}_{call_id}_meta.json"
            with open(meta_file, 'w') as f:
                json.dump({
                    'call_id': call.get('call_id'),
                    'timestamp': call.get('timestamp'),
                    'function_name': function_name,
                    'model': call.get('model'),
                    'tokens_input': call.get('tokens_input'),
                    'tokens_output': call.get('tokens_output'),
                    'latency_ms': call.get('latency_ms'),
                    'success': call.get('success'),
                    'metadata': call.get('metadata', {})
                }, f, indent=2)

            print(F.success(f"Exported call #{i}: {function_name}"))

        print(f"\n{F.success(f'Exported {len(llm_calls)} LLM calls to {output_dir}')}")

    def analyze_performance(self) -> None:
        """Analyze LLM call performance metrics."""
        data = self.load_session()
        llm_calls = data.get('llm_calls', [])

        if not llm_calls:
            print(F.warning("No LLM calls to analyze"))
            return

        print(F.header("LLM PERFORMANCE ANALYSIS"))

        # Group by function
        by_function: Dict[str, List[Dict]] = {}
        for call in llm_calls:
            func = call.get('function_name', 'unknown')
            if func not in by_function:
                by_function[func] = []
            by_function[func].append(call)

        # Analyze each function
        print(F.subheader("Performance by Function"))

        for func, calls in sorted(by_function.items()):
            print(f"\n{F.BOLD}{func}:{F.RESET}")

            call_count = len(calls)
            total_latency = sum(c.get('latency_ms', 0) for c in calls)
            avg_latency = total_latency / call_count if call_count > 0 else 0

            total_tokens = sum(c.get('tokens_input', 0) + c.get('tokens_output', 0) for c in calls)
            avg_tokens = total_tokens / call_count if call_count > 0 else 0

            print(f"  Calls: {call_count}")
            print(f"  Total Latency: {F.format_duration(total_latency)}")
            print(f"  Avg Latency: {F.format_duration(avg_latency)}")
            print(f"  Total Tokens: {total_tokens}")
            print(f"  Avg Tokens: {avg_tokens:.0f}")
