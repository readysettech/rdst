"""
Snapshot Browser - Navigate and compare session snapshots.

Usage:
    rdstdbg snapshots SESSION_ID
    rdstdbg snapshot SESSION_ID SNAPSHOT_NAME
    rdstdbg diff SESSION_ID SNAPSHOT1 SNAPSHOT2
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from .formatters import Formatter as F

logger = logging.getLogger(__name__)


class SnapshotBrowser:
    """Browse and compare session snapshots."""

    def __init__(self, snapshots_dir: Path):
        """
        Initialize snapshot browser.

        Args:
            snapshots_dir: Path to snapshots directory
        """
        self.snapshots_dir = snapshots_dir

    def list_snapshots(self) -> None:
        """List all snapshots in chronological order."""
        if not self.snapshots_dir.exists():
            print(F.warning("No snapshots directory found"))
            return

        snapshots = sorted(self.snapshots_dir.glob("*.json"))

        if not snapshots:
            print(F.warning("No snapshots found"))
            return

        print(F.header("SESSION SNAPSHOTS"))
        print(F.metric("Total Snapshots", len(snapshots)))
        print()

        print(F.subheader("Snapshot Timeline"))

        for i, snapshot_path in enumerate(snapshots):
            name = snapshot_path.name
            name_without_ext = name.replace('.json', '')
            size_kb = snapshot_path.stat().st_size / 1024

            # Parse snapshot name to extract step and phase
            parts = name_without_ext.split('_', 1)
            step_num = parts[0]
            phase = parts[1] if len(parts) > 1 else 'unknown'

            # Color code by phase
            if 'pre_' in phase:
                phase_marker = f"{F.BLUE}⬇{F.RESET}"
            elif 'post_' in phase:
                phase_marker = f"{F.GREEN}⬆{F.RESET}"
            elif 'final_' in phase:
                phase_marker = f"{F.CYAN}★{F.RESET}"
            else:
                phase_marker = " "

            # Show full name without .json extension for easy copy-paste
            print(f"  {phase_marker} {F.DIM}{name_without_ext}{F.RESET} {F.GRAY}({size_kb:.1f} KB){F.RESET}")

    def show_snapshot(self, snapshot_name: str, fields: Optional[List[str]] = None) -> None:
        """
        Show contents of a specific snapshot.

        Args:
            snapshot_name: Name of snapshot file (with or without .json)
            fields: Specific fields to show (default: all)
        """
        if not snapshot_name.endswith('.json'):
            snapshot_name += '.json'

        snapshot_path = self.snapshots_dir / snapshot_name

        if not snapshot_path.exists():
            print(F.error(f"Snapshot not found: {snapshot_name}"))
            return

        with open(snapshot_path) as f:
            data = json.load(f)

        print(F.header(f"SNAPSHOT: {snapshot_name}"))

        if fields:
            # Show only requested fields
            for field in fields:
                value = self._get_nested_field(data, field)
                print(F.label(field, value))
        else:
            # Show summary
            print(F.subheader("Summary"))
            print(F.label("Current State", F.state_name(data.get('current_state', 'unknown'))))
            print(F.label("Iteration", data.get('iteration_count', 0)))
            print(F.label("SQL", data.get('sql', '(not yet generated)') or '(not yet generated)'))

            # Show key fields
            print(F.subheader("Key Fields"))
            interesting_fields = [
                'nl_question',
                'explanation',
                'confidence',
                'intent_is_clear',
                'rows',
                'columns',
            ]

            for field in interesting_fields:
                if field in data:
                    value = data[field]
                    if isinstance(value, list):
                        print(F.label(field, f"[{len(value)} items]"))
                    elif isinstance(value, dict):
                        print(F.label(field, f"{{...}}"))
                    else:
                        print(F.label(field, F.truncate(str(value), 80)))

    def diff_snapshots(self, snapshot1: str, snapshot2: str) -> None:
        """
        Compare two snapshots and show differences.

        Args:
            snapshot1: First snapshot name
            snapshot2: Second snapshot name
        """
        if not snapshot1.endswith('.json'):
            snapshot1 += '.json'
        if not snapshot2.endswith('.json'):
            snapshot2 += '.json'

        path1 = self.snapshots_dir / snapshot1
        path2 = self.snapshots_dir / snapshot2

        if not path1.exists():
            print(F.error(f"Snapshot not found: {snapshot1}"))
            return
        if not path2.exists():
            print(F.error(f"Snapshot not found: {snapshot2}"))
            return

        with open(path1) as f:
            data1 = json.load(f)
        with open(path2) as f:
            data2 = json.load(f)

        print(F.header(f"SNAPSHOT DIFF: {snapshot1} → {snapshot2}"))

        # Compare key fields
        changes = self._find_changes(data1, data2)

        if not changes:
            print(F.success("No changes detected"))
            return

        print(F.subheader(f"Changes ({len(changes)} fields modified)"))

        for field, (old_val, new_val) in changes.items():
            # Format values
            old_str = self._format_value(old_val)
            new_str = self._format_value(new_val)

            print(f"\n{F.BOLD}{field}:{F.RESET}")
            print(f"  {F.RED}− {old_str}{F.RESET}")
            print(f"  {F.GREEN}+ {new_str}{F.RESET}")

    def _get_nested_field(self, data: Dict, field_path: str) -> Any:
        """Get nested field using dot notation (e.g., 'extra_data.hypothesis_set')."""
        parts = field_path.split('.')
        current = data

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None

        return current

    def _find_changes(self, data1: Dict, data2: Dict, prefix: str = "") -> Dict[str, tuple]:
        """
        Find changes between two dictionaries.

        Returns:
            Dict mapping field path to (old_value, new_value)
        """
        changes = {}

        # Check all keys in data2
        all_keys = set(data1.keys()) | set(data2.keys())

        # Ignore certain fields
        ignore_fields = {'updated_at', 'timestamp', 'state_transitions', 'iteration_count'}

        for key in all_keys:
            if key in ignore_fields:
                continue

            field_path = f"{prefix}.{key}" if prefix else key

            if key not in data1:
                # New field
                changes[field_path] = (None, data2[key])
            elif key not in data2:
                # Removed field
                changes[field_path] = (data1[key], None)
            elif data1[key] != data2[key]:
                # Changed field
                val1 = data1[key]
                val2 = data2[key]

                # If both are dicts, recurse
                if isinstance(val1, dict) and isinstance(val2, dict):
                    nested_changes = self._find_changes(val1, val2, field_path)
                    changes.update(nested_changes)
                else:
                    changes[field_path] = (val1, val2)

        return changes

    def _format_value(self, value: Any, max_len: int = 100) -> str:
        """Format value for diff display."""
        if value is None:
            return f"{F.GRAY}(null){F.RESET}"
        elif isinstance(value, bool):
            return f"{F.CYAN}{value}{F.RESET}"
        elif isinstance(value, (int, float)):
            return f"{F.YELLOW}{value}{F.RESET}"
        elif isinstance(value, str):
            return F.truncate(value, max_len)
        elif isinstance(value, list):
            return f"{F.DIM}[{len(value)} items]{F.RESET}"
        elif isinstance(value, dict):
            return f"{F.DIM}{{{len(value)} keys}}{F.RESET}"
        else:
            return str(value)
