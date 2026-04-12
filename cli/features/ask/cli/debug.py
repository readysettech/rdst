"""Ask session debugging command."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from shared.constants import RDST_DATA_DIR

from features.ask.debug import (
    Formatter as F,
    LLMInspector,
    SessionInspector,
    SnapshotBrowser,
    StateViewer,
)


class RdstDbgCommand:
    """Debug command for analyzing ask3 sessions."""

    def __init__(self, sessions_root: Optional[Path] = None):
        self.sessions_root = sessions_root or RDST_DATA_DIR / "sessions"

    def find_session_dir(self, session_id: str) -> Optional[Path]:
        if not self.sessions_root.exists():
            print(F.error(f"Sessions directory not found: {self.sessions_root}"))
            return None
        for session_dir in self.sessions_root.iterdir():
            if session_dir.is_dir() and session_dir.name.startswith(session_id):
                return session_dir
        print(F.error(f"Session not found: {session_id}"))
        print(F.info(f"Search in: {self.sessions_root}"))
        return None

    def cmd_inspect(self, session_id: str, verbose: bool = False) -> int:
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1
        SessionInspector(session_dir).inspect(verbose=verbose)
        return 0

    def cmd_transitions(self, session_id: str, verbose: bool = False) -> int:
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1
        StateViewer(session_dir / "session.json").show_transitions(
            verbose=verbose, unique_only=True
        )
        return 0

    def cmd_snapshots(self, session_id: str) -> int:
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1
        SnapshotBrowser(session_dir / "snapshots").list_snapshots()
        return 0

    def cmd_snapshot(
        self, session_id: str, snapshot_name: str, fields: Optional[list] = None
    ) -> int:
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1
        SnapshotBrowser(session_dir / "snapshots").show_snapshot(
            snapshot_name, fields=fields
        )
        return 0

    def cmd_diff(self, session_id: str, snapshot1: str, snapshot2: str) -> int:
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1
        SnapshotBrowser(session_dir / "snapshots").diff_snapshots(snapshot1, snapshot2)
        return 0

    def cmd_llm(
        self,
        session_id: str,
        call_id: Optional[str] = None,
        call_index: Optional[int] = None,
        export_dir: Optional[str] = None,
    ) -> int:
        session_dir = self.find_session_dir(session_id)
        if not session_dir:
            return 1

        inspector = LLMInspector(session_dir / "session.json")
        if export_dir:
            inspector.export_prompts(Path(export_dir))
        elif call_id or call_index is not None:
            inspector.show_call(call_id=call_id, call_index=call_index)
        else:
            inspector.list_calls()
        return 0


def main(args: list) -> int:
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
            return dbg.cmd_inspect(args[2], verbose="--verbose" in args or "-v" in args)
        if command == "transitions":
            if len(args) < 3:
                print(F.error("Usage: rdstdbg transitions SESSION_ID [--verbose]"))
                return 1
            return dbg.cmd_transitions(args[2], verbose="--verbose" in args or "-v" in args)
        if command == "snapshots":
            if len(args) < 3:
                print(F.error("Usage: rdstdbg snapshots SESSION_ID"))
                return 1
            return dbg.cmd_snapshots(args[2])
        if command == "snapshot":
            if len(args) < 4:
                print(F.error("Usage: rdstdbg snapshot SESSION_ID SNAPSHOT_NAME"))
                return 1
            return dbg.cmd_snapshot(args[2], args[3])
        if command == "diff":
            if len(args) < 5:
                print(F.error("Usage: rdstdbg diff SESSION_ID SNAPSHOT1 SNAPSHOT2"))
                return 1
            return dbg.cmd_diff(args[2], args[3], args[4])
        if command == "llm":
            if len(args) < 3:
                print(F.error("Usage: rdstdbg llm SESSION_ID [--call-id ID|--export DIR]"))
                return 1
            call_id = None
            call_index = None
            export_dir = None
            if "--call-id" in args:
                idx = args.index("--call-id")
                if idx + 1 < len(args):
                    call_id = args[idx + 1]
            if "--call-index" in args:
                idx = args.index("--call-index")
                if idx + 1 < len(args):
                    call_index = int(args[idx + 1])
            if "--export" in args:
                idx = args.index("--export")
                if idx + 1 < len(args):
                    export_dir = args[idx + 1]
            return dbg.cmd_llm(args[2], call_id=call_id, call_index=call_index, export_dir=export_dir)
        print(F.error(f"Unknown command: {command}"))
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted")
        return 130
