"""
Session Inspector - High-level session summary and analysis.

Usage:
    rdstdbg inspect SESSION_ID
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

from .formatters import Formatter as F

logger = logging.getLogger(__name__)


class SessionInspector:
    """Inspect and summarize ask3 sessions."""

    def __init__(self, session_dir: Path):
        """
        Initialize inspector.

        Args:
            session_dir: Path to session directory
        """
        self.session_dir = session_dir
        self.session_file = session_dir / "session.json"
        self.snapshots_dir = session_dir / "snapshots"
        self._session_data: Optional[Dict[str, Any]] = None

    @classmethod
    def from_session_id(cls, session_id: str, sessions_root: Optional[Path] = None) -> SessionInspector:
        """
        Create inspector from session ID.

        Args:
            session_id: Full or partial session ID
            sessions_root: Root directory for sessions (default: ~/.rdst/sessions/)

        Returns:
            SessionInspector instance
        """
        if sessions_root is None:
            sessions_root = Path.home() / ".rdst" / "sessions"

        # Find matching session directory
        for session_dir in sessions_root.iterdir():
            if session_dir.is_dir() and session_dir.name.startswith(session_id):
                return cls(session_dir)

        raise ValueError(f"Session not found: {session_id}")

    def load_session(self) -> Dict[str, Any]:
        """Load session data from JSON."""
        if self._session_data is None:
            with open(self.session_file) as f:
                self._session_data = json.load(f)
        return self._session_data

    def inspect(self, verbose: bool = False) -> None:
        """
        Print high-level session summary.

        Args:
            verbose: Show detailed information
        """
        data = self.load_session()

        # Header
        print(F.header("SESSION INSPECTION"))

        # Basic info
        print(F.subheader("Basic Information"))
        print(F.label("Session ID", F.session_id(data.get('session_id', 'unknown'), short=False)))
        print(F.label("Question", data.get('nl_question', 'N/A')))
        print(F.label("Target", data.get('target', 'N/A')))
        print(F.label("Database", f"{data.get('database_engine', 'unknown')} / {data.get('target_database', 'N/A')}"))

        # Timestamps
        created = data.get('created_at', '')
        updated = data.get('updated_at', '')
        print(F.label("Created", created))
        print(F.label("Updated", updated))

        if created and updated:
            duration = self._calculate_duration(created, updated)
            print(F.label("Duration", F.format_duration(duration)))

        # SQL and results
        print(F.subheader("Generated SQL"))
        sql = data.get('sql', 'N/A')
        if sql != 'N/A':
            print(F.sql(sql))
        else:
            print(f"{F.DIM}(No SQL generated){F.RESET}")

        explanation = data.get('explanation', '')
        if explanation:
            print(F.label("\nExplanation", explanation))

        # Results summary
        print(F.subheader("Results"))
        rows_data = data.get('rows', [])
        columns = data.get('columns', [])

        print(F.metric("Rows", len(rows_data)))
        print(F.metric("Columns", len(columns)))
        if columns:
            print(F.label("Column Names", ', '.join(columns[:10]) + ('...' if len(columns) > 10 else '')))

        # Metrics
        print(F.subheader("Session Metrics"))
        print(F.metric("Iterations", data.get('iteration_count', 0)))
        print(F.metric("LLM Calls", len(data.get('llm_calls', []))))
        print(F.metric("State Transitions", len(data.get('state_transitions', []))))

        # LLM token summary
        llm_calls = data.get('llm_calls', [])
        if llm_calls:
            total_tokens = sum(call.get('tokens_input', 0) + call.get('tokens_output', 0) for call in llm_calls)
            total_latency = sum(call.get('latency_ms', 0) for call in llm_calls)
            print(F.metric("Total Tokens", total_tokens))
            print(F.metric("Total LLM Latency", F.format_duration(total_latency)))

        # Hypothesis and clarifications
        hypothesis_set = data.get('hypothesis_set')
        if hypothesis_set:
            print(F.subheader("Hypotheses"))
            hypotheses = hypothesis_set.get('hypotheses', [])
            print(F.metric("Hypotheses Generated", len(hypotheses)))
            if hypotheses:
                selected_idx = hypothesis_set.get('recommended_index', 0)
                selected = hypotheses[selected_idx] if selected_idx < len(hypotheses) else None
                if selected:
                    print(F.label("Selected", selected.get('interpretation', 'N/A')))
                    print(F.label("Confidence", f"{selected.get('confidence', 0):.0%}"))

        # Clarification loops
        transitions = data.get('state_transitions', [])
        loop_count = self._count_clarification_loops(transitions)
        if loop_count > 0:
            print(F.subheader("Clarifications"))
            print(F.metric("Clarification Loops", loop_count))

        # Validation info
        reasonableness = data.get('reasonableness')
        if reasonableness:
            print(F.subheader("Validation"))
            is_reasonable = reasonableness.get('is_reasonable', False)
            score = reasonableness.get('score', 0)
            if is_reasonable:
                print(F.success(f"Reasonableness Check: PASSED (score: {score:.0%})"))
            else:
                print(F.warning(f"Reasonableness Check: FLAGGED (score: {score:.0%})"))

            concerns = reasonableness.get('concerns', [])
            if concerns:
                print(F.label("Concerns", len(concerns)))
                for concern in concerns[:3]:  # Show first 3
                    print(f"  • {concern.get('description', 'N/A')}")

        user_rating = data.get('user_accuracy_rating')
        if user_rating:
            print(F.label("User Accuracy Rating", user_rating))

        # Verbose mode - show snapshots
        if verbose and self.snapshots_dir.exists():
            snapshots = sorted(self.snapshots_dir.glob("*.json"))
            print(F.subheader("Snapshots"))
            print(F.metric("Snapshot Files", len(snapshots)))
            if snapshots:
                print("\nSnapshot Timeline:")
                for i, snapshot in enumerate(snapshots[:10]):  # Show first 10
                    print(f"  {i+1:3d}. {snapshot.name}")
                if len(snapshots) > 10:
                    print(f"  ... and {len(snapshots) - 10} more")

        # Final state
        print(F.subheader("Final State"))
        current_state = data.get('current_state', 'unknown')
        print(F.label("Current State", F.state_name(current_state)))

        if current_state == 'error':
            error = data.get('last_error', 'Unknown error')
            print(F.error(f"Error: {error}"))

    def _calculate_duration(self, start: str, end: str) -> float:
        """Calculate duration in milliseconds between two ISO timestamps."""
        try:
            start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
            return (end_dt - start_dt).total_seconds() * 1000
        except Exception:
            return 0.0

    def _count_clarification_loops(self, transitions: list) -> int:
        """Count number of clarification loops (collecting_clarifications -> generating_hypotheses)."""
        count = 0
        for t in transitions:
            if (t.get('from_state') == 'collecting_clarifications' and
                t.get('to_state') == 'generating_hypotheses'):
                count += 1
        return count
