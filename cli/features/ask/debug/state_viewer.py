"""
State Viewer - View and analyze state transitions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .formatters import Formatter as F

logger = logging.getLogger(__name__)


class StateViewer:
    """View and analyze state transitions."""

    def __init__(self, session_file: Path):
        self.session_file = session_file
        self._session_data: Optional[Dict[str, Any]] = None

    def load_session(self) -> Dict[str, Any]:
        if self._session_data is None:
            with open(self.session_file, encoding="utf-8") as f:
                self._session_data = json.load(f)
        return self._session_data

    def show_transitions(self, verbose: bool = False, unique_only: bool = True) -> None:
        data = self.load_session()
        transitions = data.get("state_transitions", [])
        if not transitions:
            print(F.warning("No state transitions found"))
            return

        print(F.header("STATE TRANSITIONS"))
        print(F.label("Session", F.session_id(data.get("session_id", "unknown"), short=False)))
        print(F.label("Question", data.get("nl_question", "N/A")))
        print(F.metric("Total Transitions", len(transitions)))
        print()

        loop_count, loop_positions = self._find_clarification_loops(transitions)
        if loop_count > 0:
            print(F.info(f"Found {loop_count} clarification loop(s)"))
            print()

        print(F.subheader("Transition Timeline"))
        prev_from = None
        prev_to = None
        step_num = 0

        for i, transition in enumerate(transitions):
            from_state = transition.get("from_state", "unknown")
            to_state = transition.get("to_state", "unknown")
            timestamp = transition.get("timestamp", "")
            description = transition.get("description", "")

            if unique_only and from_state == to_state:
                continue
            if unique_only and from_state == prev_from and to_state == prev_to:
                continue

            step_num += 1
            time_str = F.timestamp(timestamp)
            is_loop = i in loop_positions
            loop_marker = f" {F.YELLOW}← CLARIFICATION LOOP{F.RESET}" if is_loop else ""
            from_formatted = F.state_name(from_state)
            to_formatted = F.state_name(to_state)
            print(f"{step_num:3d}. {time_str} {from_formatted:50s} → {to_formatted:50s}{loop_marker}")
            if verbose and description:
                print(f"     {F.DIM}{description}{F.RESET}")

            prev_from = from_state
            prev_to = to_state

        print(F.subheader("Summary"))
        self._print_state_summary(transitions)

    def _find_clarification_loops(self, transitions: List[Dict]) -> Tuple[int, List[int]]:
        loop_count = 0
        loop_positions = []
        for i, transition in enumerate(transitions):
            if (
                transition.get("from_state") == "collecting_clarifications"
                and transition.get("to_state") == "generating_hypotheses"
            ):
                loop_count += 1
                loop_positions.append(i)
        return loop_count, loop_positions

    def _print_state_summary(self, transitions: List[Dict]) -> None:
        state_times: Dict[str, float] = {}
        state_counts: Dict[str, int] = {}

        for i, transition in enumerate(transitions):
            from_state = transition.get("from_state", "unknown")
            state_counts[from_state] = state_counts.get(from_state, 0) + 1

            if i < len(transitions) - 1:
                try:
                    current_time = datetime.fromisoformat(
                        transition.get("timestamp", "").replace("Z", "+00:00")
                    )
                    next_time = datetime.fromisoformat(
                        transitions[i + 1].get("timestamp", "").replace("Z", "+00:00")
                    )
                    duration_ms = (next_time - current_time).total_seconds() * 1000
                    state_times[from_state] = state_times.get(from_state, 0) + duration_ms
                except Exception:
                    pass

        print(F.metric("Unique States Visited", len(state_counts)))
        print("\nTop States by Visit Count:")
        sorted_states = sorted(state_counts.items(), key=lambda x: x[1], reverse=True)
        for state, count in sorted_states[:10]:
            print(f"  {F.state_name(state):50s} {F.metric('visits', count)}")

        if state_times:
            print("\nTop States by Time Spent:")
            sorted_times = sorted(state_times.items(), key=lambda x: x[1], reverse=True)
            for state, duration_ms in sorted_times[:10]:
                print(f"  {F.state_name(state):50s} {F.format_duration(duration_ms)}")

    def show_state_flow(self) -> None:
        data = self.load_session()
        transitions = data.get("state_transitions", [])
        if not transitions:
            print(F.warning("No state transitions found"))
            return

        print(F.header("STATE FLOW DIAGRAM"))
        edges: Dict[Tuple[str, str], int] = {}
        current = transitions[0].get("from_state") if transitions else None

        for transition in transitions:
            from_state = transition.get("from_state")
            to_state = transition.get("to_state")
            if from_state and to_state and from_state != to_state:
                edge = (from_state, to_state)
                edges[edge] = edges.get(edge, 0) + 1

        print("State Flow (showing unique transitions):\n")
        visited = set()
        while current:
            if current in visited:
                break
            visited.add(current)
            outgoing = [
                (to_state, count)
                for (from_state, to_state), count in edges.items()
                if from_state == current
            ]

            if not outgoing:
                print(f"  {F.state_name(current)}")
                break

            outgoing.sort(key=lambda x: x[1], reverse=True)
            for to_state, count in outgoing:
                marker = f" ({count}x)" if count > 1 else ""
                print(f"  {F.state_name(current)} → {F.state_name(to_state)}{marker}")
            current = outgoing[0][0]
