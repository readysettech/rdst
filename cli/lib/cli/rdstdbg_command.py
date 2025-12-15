"""
rdstdbg - Debug tool for rdst ask3 sessions.

Commands:
    inspect SESSION_ID              - Show session summary
    transitions SESSION_ID          - Show state transitions
    snapshots SESSION_ID            - List session snapshots
    snapshot SESSION_ID NAME        - Show specific snapshot
    diff SESSION_ID SNAP1 SNAP2     - Compare two snapshots
    llm SESSION_ID                  - List LLM calls
    llm SESSION_ID --call-id ID     - Show specific LLM call details
    llm SESSION_ID --export DIR     - Export all LLM prompts/responses

Usage:
    python rdst.py dbg inspect 07d25f6d
    python rdst.py dbg transitions 07d25f6d --verbose
    python rdst.py dbg snapshots 07d25f6d
    python rdst.py dbg llm 07d25f6d
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path
from typing import Optional

from ..debug import SessionInspector, StateViewer, SnapshotBrowser, LLMInspector, Formatter as F

logger = logging.getLogger(__name__)


class RdstDbgCommand:
    """Debug command for analyzing ask3 sessions."""

    def __init__(self, sessions_root: Optional[Path] = None):
        """
        Initialize debug command.

        Args:
            sessions_root: Root directory for sessions (default: ~/.rdst/sessions/)
        """
        self.sessions_root = sessions_root or Path.home() / ".rdst" / "sessions"

    def find_session_dir(self, session_id: str) -> Optional[Path]:
        """
        Find session directory by ID (full or partial).

        Args:
            session_id: Full or partial session ID

        Returns:
            Path to session directory or None if not found
        """
        if not self.sessions_root.exists():
            print(F.error(f"Sessions directory not found: {self.sessions_root}"))
            return None

        # Find matching session
        for session_dir in self.sessions_root.iterdir():
            if session_dir.is_dir() and session_dir.name.startswith(session_id):
                return session_dir

        print(F.error(f"Session not found: {session_id}"))
        print(F.info(f"Search in: {self.sessions_root}"))
        return None

    def cmd_inspect(self, session_id: str, verbose: bool = False) -> int:
        """
        Inspect session summary.

        Args:
            session_id: Session ID (full or partial)
            verbose: Show detailed information

        Returns:
            Exit code
        """
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1

        inspector = SessionInspector(session_dir)
        inspector.inspect(verbose=verbose)
        return 0

    def cmd_transitions(self, session_id: str, verbose: bool = False) -> int:
        """
        Show state transitions.

        Args:
            session_id: Session ID (full or partial)
            verbose: Show detailed information

        Returns:
            Exit code
        """
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1

        session_file = session_dir / "session.json"
        viewer = StateViewer(session_file)
        viewer.show_transitions(verbose=verbose, unique_only=True)
        return 0

    def cmd_snapshots(self, session_id: str) -> int:
        """
        List session snapshots.

        Args:
            session_id: Session ID (full or partial)

        Returns:
            Exit code
        """
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1

        snapshots_dir = session_dir / "snapshots"
        browser = SnapshotBrowser(snapshots_dir)
        browser.list_snapshots()
        return 0

    def cmd_snapshot(self, session_id: str, snapshot_name: str, fields: Optional[list] = None) -> int:
        """
        Show specific snapshot.

        Args:
            session_id: Session ID (full or partial)
            snapshot_name: Snapshot filename
            fields: Specific fields to show

        Returns:
            Exit code
        """
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1

        snapshots_dir = session_dir / "snapshots"
        browser = SnapshotBrowser(snapshots_dir)
        browser.show_snapshot(snapshot_name, fields=fields)
        return 0

    def cmd_diff(self, session_id: str, snapshot1: str, snapshot2: str) -> int:
        """
        Compare two snapshots.

        Args:
            session_id: Session ID (full or partial)
            snapshot1: First snapshot name
            snapshot2: Second snapshot name

        Returns:
            Exit code
        """
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1

        snapshots_dir = session_dir / "snapshots"
        browser = SnapshotBrowser(snapshots_dir)
        browser.diff_snapshots(snapshot1, snapshot2)
        return 0

    def cmd_llm(self, session_id: str, call_id: Optional[str] = None,
                call_index: Optional[int] = None, export_dir: Optional[str] = None) -> int:
        """
        Inspect LLM calls.

        Args:
            session_id: Session ID (full or partial)
            call_id: Specific call ID to show
            call_index: Specific call index (1-based)
            export_dir: Directory to export prompts/responses

        Returns:
            Exit code
        """
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1

        session_file = session_dir / "session.json"
        inspector = LLMInspector(session_file)

        if export_dir:
            # Export mode
            output_path = Path(export_dir)
            inspector.export_prompts(output_path)
        elif call_id or call_index is not None:
            # Show specific call
            inspector.show_call(call_id=call_id, call_index=call_index)
        else:
            # List all calls
            inspector.list_calls()

        return 0


def main(args: list) -> int:
    """
    Main entry point for rdstdbg command.

    Args:
        args: Command-line arguments

    Returns:
        Exit code
    """
    if len(args) < 2:
        print("Usage: python rdst.py dbg COMMAND SESSION_ID [OPTIONS]")
        print("\nCommands:")
        print("  inspect SESSION_ID              - Show session summary")
        print("  transitions SESSION_ID          - Show state transitions")
        print("  snapshots SESSION_ID            - List session snapshots")
        print("  snapshot SESSION_ID NAME        - Show specific snapshot")
        print("  diff SESSION_ID SNAP1 SNAP2     - Compare two snapshots")
        print("  llm SESSION_ID                  - List LLM calls")
        print("  llm SESSION_ID --call-id ID     - Show specific LLM call")
        print("  llm SESSION_ID --export DIR     - Export LLM prompts")
        return 1

    command = args[1]
    dbg = RdstDbgCommand()

    try:
        if command == "inspect":
            if len(args) < 3:
                print(F.error("Usage: rdstdbg inspect SESSION_ID [--verbose]"))
                return 1
            session_id = args[2]
            verbose = "--verbose" in args or "-v" in args
            return dbg.cmd_inspect(session_id, verbose=verbose)

        elif command == "transitions":
            if len(args) < 3:
                print(F.error("Usage: rdstdbg transitions SESSION_ID [--verbose]"))
                return 1
            session_id = args[2]
            verbose = "--verbose" in args or "-v" in args
            return dbg.cmd_transitions(session_id, verbose=verbose)

        elif command == "snapshots":
            if len(args) < 3:
                print(F.error("Usage: rdstdbg snapshots SESSION_ID"))
                return 1
            session_id = args[2]
            return dbg.cmd_snapshots(session_id)

        elif command == "snapshot":
            if len(args) < 4:
                print(F.error("Usage: rdstdbg snapshot SESSION_ID SNAPSHOT_NAME"))
                return 1
            session_id = args[2]
            snapshot_name = args[3]
            return dbg.cmd_snapshot(session_id, snapshot_name)

        elif command == "diff":
            if len(args) < 5:
                print(F.error("Usage: rdstdbg diff SESSION_ID SNAPSHOT1 SNAPSHOT2"))
                return 1
            session_id = args[2]
            snapshot1 = args[3]
            snapshot2 = args[4]
            return dbg.cmd_diff(session_id, snapshot1, snapshot2)

        elif command == "llm":
            if len(args) < 3:
                print(F.error("Usage: rdstdbg llm SESSION_ID [--call-id ID | --export DIR]"))
                return 1
            session_id = args[2]

            # Parse options
            call_id = None
            export_dir = None
            call_index = None

            i = 3
            while i < len(args):
                if args[i] == "--call-id" and i + 1 < len(args):
                    value = args[i + 1]
                    # Check if it's a number (sequence index) or a call ID
                    if value.isdigit():
                        call_index = int(value)
                    else:
                        call_id = value
                    i += 2
                elif args[i] == "--export" and i + 1 < len(args):
                    export_dir = args[i + 1]
                    i += 2
                elif args[i] == "--index" and i + 1 < len(args):
                    call_index = int(args[i + 1])
                    i += 2
                else:
                    i += 1

            return dbg.cmd_llm(session_id, call_id=call_id, call_index=call_index, export_dir=export_dir)

        else:
            print(F.error(f"Unknown command: {command}"))
            return 1

    except Exception as e:
        logger.exception("Error executing command")
        print(F.error(f"Error: {e}"))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
