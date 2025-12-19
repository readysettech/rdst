"""
RDST Scan Context Gatherer

Follows imports to gather ALL relevant context for query extraction.
This is the deterministic part - no LLM needed here.
"""

import os
import re
import ast
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple


class ContextGatherer:
    """
    Gathers all relevant context for a set of changed files.

    Given a list of changed files (from git diff), this:
    1. Identifies which files contain ORM patterns
    2. Follows imports to find model definitions
    3. Collects all relevant code into a single context bundle
    """

    # ORM patterns that indicate a file has queries
    ORM_QUERY_PATTERNS = [
        r"\.query\(",
        r"\.filter\(",
        r"\.filter_by\(",
        r"\.join\(",
        r"\.outerjoin\(",
        r"\.group_by\(",
        r"\.order_by\(",
        r"\.all\(\)",
        r"\.first\(\)",
        r"\.scalar\(",
        r"session\.(query|execute)",
        r"db\.(query|execute|session)",
        r"\.objects\.",  # Django
        r"execute\(['\"]SELECT",
        r"text\(['\"]",
    ]

    # Patterns that indicate model definitions
    MODEL_PATTERNS = [
        r"class\s+\w+\(.*Base\)",  # SQLAlchemy declarative
        r"class\s+\w+\(.*Model\)",  # Django/Flask models
        r"Column\(",
        r"relationship\(",
        r"ForeignKey\(",
        r"__tablename__",
    ]

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()
        self.visited_files: Set[str] = set()
        self.model_files: Dict[str, str] = {}  # path -> content
        self.query_files: Dict[str, str] = {}  # path -> content

    def gather_context(self, changed_files: List[str]) -> Dict:
        """
        Main entry point. Given changed files, gather all context.

        Returns:
            {
                "query_files": {path: content},  # Files with queries
                "model_files": {path: content},  # Model definitions
                "combined_context": str,         # Everything bundled for LLM
            }
        """
        self.visited_files = set()
        self.model_files = {}
        self.query_files = {}

        # Step 1: Find which changed files have ORM queries
        for filepath in changed_files:
            full_path = self.repo_root / filepath
            if not full_path.exists():
                continue

            content = self._read_file(full_path)
            if not content:
                continue

            if self._has_orm_queries(content):
                self.query_files[filepath] = content

                # Step 2: Follow imports to find models
                self._follow_imports(full_path, content)

        # Step 3: Bundle everything into combined context
        combined = self._build_combined_context()

        return {
            "query_files": self.query_files,
            "model_files": self.model_files,
            "combined_context": combined,
            "stats": {
                "query_files_count": len(self.query_files),
                "model_files_count": len(self.model_files),
                "total_lines": sum(c.count('\n') for c in self.query_files.values()) +
                              sum(c.count('\n') for c in self.model_files.values()),
            }
        }

    def _read_file(self, path: Path) -> Optional[str]:
        """Read file content safely."""
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

    def _has_orm_queries(self, content: str) -> bool:
        """Check if content has ORM query patterns."""
        for pattern in self.ORM_QUERY_PATTERNS:
            if re.search(pattern, content):
                return True
        return False

    def _has_model_definitions(self, content: str) -> bool:
        """Check if content has model definitions."""
        for pattern in self.MODEL_PATTERNS:
            if re.search(pattern, content):
                return True
        return False

    def _follow_imports(self, file_path: Path, content: str):
        """
        Parse imports and follow them to find model files.
        """
        if str(file_path) in self.visited_files:
            return
        self.visited_files.add(str(file_path))

        # Parse the file to extract imports
        try:
            tree = ast.parse(content)
        except SyntaxError:
            # Fall back to regex for non-Python or invalid syntax
            self._follow_imports_regex(file_path, content)
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._resolve_import(file_path, alias.name, level=0)

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                level = node.level  # Number of dots in relative import
                self._resolve_import(file_path, module, level=level)

    def _follow_imports_regex(self, file_path: Path, content: str):
        """Fallback import following using regex."""
        # Match: from X import Y  or  import X
        import_patterns = [
            r"from\s+([\w.]+)\s+import",
            r"import\s+([\w.]+)",
        ]

        for pattern in import_patterns:
            for match in re.finditer(pattern, content):
                module = match.group(1)
                self._resolve_import(file_path, module, is_from="from" in pattern)

    def _resolve_import(self, from_file: Path, module: str, level: int = 0):
        """
        Resolve an import to actual file path and read if it's a model file.

        Args:
            from_file: The file containing the import
            module: The module name (e.g., "models" for "from ..models import X")
            level: Number of dots in relative import (0=absolute, 1=., 2=.., etc.)
        """
        candidate_paths = []

        if level > 0:
            # Relative import: level indicates number of dots
            # . (level=1) = current package (file.parent)
            # .. (level=2) = parent package (go up 1 from file.parent)
            # So go up (level - 1) from file.parent
            base_dir = from_file.parent
            for _ in range(level - 1):
                base_dir = base_dir.parent

            if module:
                # from ..models import X  ->  go up 2, then into 'models'
                candidate_paths = [
                    base_dir / module.replace('.', '/') / "__init__.py",
                    base_dir / (module.replace('.', '/') + ".py"),
                    base_dir / module.replace('.', '/'),  # directory itself
                ]
            else:
                # from .. import X  ->  just go up 2
                candidate_paths = [base_dir / "__init__.py"]
        else:
            # Absolute import - look relative to repo root
            candidate_paths = [
                self.repo_root / module.replace('.', '/') / "__init__.py",
                self.repo_root / (module.replace('.', '/') + ".py"),
            ]

            # Also check relative to file's directory (for local imports)
            candidate_paths.extend([
                from_file.parent / module.replace('.', '/') / "__init__.py",
                from_file.parent / (module.replace('.', '/') + ".py"),
            ])

        # Try each candidate
        for candidate in candidate_paths:
            if candidate.exists() and candidate.is_file():
                self._process_potential_model_file(candidate)
                break
            elif candidate.exists() and candidate.is_dir():
                # It's a package, check __init__.py and all .py files
                init_file = candidate / "__init__.py"
                if init_file.exists():
                    self._process_potential_model_file(init_file)

                # Also check all Python files in the directory
                for py_file in candidate.glob("*.py"):
                    if py_file.name != "__init__.py":
                        self._process_potential_model_file(py_file)
                break

    def _process_potential_model_file(self, file_path: Path):
        """Check if file has model definitions and add to context."""
        # Check visited before reading file (avoid duplicate work)
        if str(file_path) in self.visited_files:
            return

        content = self._read_file(file_path)
        if not content:
            return

        # Follow imports FIRST (this will mark as visited)
        # This finds related model files recursively
        self._follow_imports(file_path, content)

        # Check if this file has model definitions
        if self._has_model_definitions(content):
            try:
                rel_path = str(file_path.relative_to(self.repo_root))
            except ValueError:
                rel_path = str(file_path)

            self.model_files[rel_path] = content

    def _build_combined_context(self) -> str:
        """
        Build the combined context string to send to LLM.

        Format:
        === MODEL DEFINITIONS ===

        # File: models/customer.py
        <content>

        === QUERY CODE ===

        # File: services/customer_service.py
        <content>
        """
        sections = []

        # Add model definitions first (so LLM understands the schema)
        if self.model_files:
            sections.append("=== MODEL DEFINITIONS ===\n")
            for path, content in sorted(self.model_files.items()):
                sections.append(f"# File: {path}")
                sections.append(content)
                sections.append("")

        # Add query code
        if self.query_files:
            sections.append("=== CODE WITH QUERIES ===\n")
            for path, content in sorted(self.query_files.items()):
                sections.append(f"# File: {path}")
                sections.append(content)
                sections.append("")

        return "\n".join(sections)


def gather_context_for_diff(repo_root: str, diff_files: List[str]) -> Dict:
    """
    Convenience function to gather context for a git diff.

    Usage:
        # Get changed files from git
        changed = subprocess.check_output(['git', 'diff', '--name-only', 'HEAD~1']).decode().strip().split('\n')

        # Gather context
        context = gather_context_for_diff('/path/to/repo', changed)

        # Send to LLM
        send_to_llm(context['combined_context'])
    """
    gatherer = ContextGatherer(repo_root)
    return gatherer.gather_context(diff_files)


# Test/demo
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python scan_context.py <repo_root> <file1> [file2] ...")
        print("Example: python scan_context.py /path/to/repo services/customer_service.py")
        sys.exit(1)

    repo_root = sys.argv[1]
    files = sys.argv[2:]

    result = gather_context_for_diff(repo_root, files)

    print(f"Query files found: {result['stats']['query_files_count']}")
    print(f"Model files found: {result['stats']['model_files_count']}")
    print(f"Total lines: {result['stats']['total_lines']}")
    print()
    print("Files gathered:")
    for f in result['query_files']:
        print(f"  [QUERY] {f}")
    for f in result['model_files']:
        print(f"  [MODEL] {f}")
    print()
    print("=" * 60)
    print("COMBINED CONTEXT:")
    print("=" * 60)
    print(result['combined_context'][:3000])
    if len(result['combined_context']) > 3000:
        print(f"\n... ({len(result['combined_context']) - 3000} more characters)")
