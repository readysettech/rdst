"""
Demo command — try RDST with a demo database.

Subcommands:
  setup     Start PostgreSQL Docker container
  load      Download and load DBA StackExchange data
  tour      Interactive guided walkthrough
  teardown  Stop and remove demo container
"""

from __future__ import annotations

import os
import subprocess
import time

from lib.ui import StyleTokens, get_console, MessagePanel, NextSteps
from lib.cli.rdst_cli import RdstResult


DEMO_CONTAINER = "rdst-demo-pg"
DEMO_PORT = 15432
DEMO_USER = "postgres"
DEMO_PASSWORD = "rdst_demo"
DEMO_DATABASE = "demo"
DEMO_TARGET = "demo"
DEMO_SITE = "dba.stackexchange.com"

# Queries for load generation (mix of fast, medium, slow)
DEMO_QUERIES = {
    "demo_fast": "SELECT count(*) FROM tags",
    "demo_medium": "SELECT title, score FROM posts WHERE score > 50 ORDER BY score DESC LIMIT 20",
    "demo_scan": "SELECT p.title, u.displayname FROM posts p JOIN users u ON p.owneruserid = u.id WHERE p.posttypeid = 1 LIMIT 100",
    "demo_agg": "SELECT posttypeid, count(*), avg(score) FROM posts GROUP BY posttypeid",
    "demo_heavy": "SELECT u.displayname, count(*) FROM comments c JOIN users u ON c.userid = u.id GROUP BY u.displayname ORDER BY count(*) DESC LIMIT 10",
}


class DemoCommand:
    def __init__(self):
        self.console = get_console()

    def run(self, subcommand: str | None, **kwargs) -> RdstResult:
        if not subcommand:
            self._print_help()
            return RdstResult(True)

        dispatch = {
            "setup": self._setup,
            "load": self._load,
            "tour": self._tour,
            "teardown": self._teardown,
        }
        handler = dispatch.get(subcommand)
        if not handler:
            return RdstResult(False, f"Unknown demo subcommand: {subcommand}")
        return handler(**kwargs)

    def _print_help(self):
        self.console.print()
        self.console.print(f"[{StyleTokens.EMPHASIS}]rdst demo[/{StyleTokens.EMPHASIS}] — try RDST with a demo database")
        self.console.print()
        steps = [
            ("rdst demo setup", "Start PostgreSQL Docker container"),
            ("rdst demo load", "Download and load demo data (~2M rows)"),
            ("rdst demo tour", "Interactive guided walkthrough"),
            ("rdst demo teardown", "Clean up when done"),
        ]
        self.console.print(NextSteps(steps))

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def _setup(self, **_kwargs) -> RdstResult:
        """Start a PostgreSQL Docker container for the demo."""
        # Check Docker
        if not self._docker_available():
            return RdstResult(False, "Docker is not running. Please start Docker and try again.")

        # Check for existing container
        status = self._container_status()
        if status == "running":
            self.console.print(MessagePanel("Demo container is already running", variant="success"))
            self._print_connection_info()
            return RdstResult(True)

        if status == "stopped":
            self.console.print(f"[{StyleTokens.MUTED}]Restarting stopped demo container...[/{StyleTokens.MUTED}]")
            subprocess.run(["docker", "start", DEMO_CONTAINER], capture_output=True)
        else:
            self.console.print(f"[{StyleTokens.MUTED}]Starting PostgreSQL 17 container...[/{StyleTokens.MUTED}]")
            subprocess.run([
                "docker", "run", "-d",
                "--name", DEMO_CONTAINER,
                "-p", f"{DEMO_PORT}:5432",
                "-e", f"POSTGRES_PASSWORD={DEMO_PASSWORD}",
                "-e", f"POSTGRES_DB={DEMO_DATABASE}",
                "postgres:17",
                "-c", "shared_preload_libraries=pg_stat_statements",
            ], capture_output=True)

        # Wait for readiness
        if not self._wait_for_pg(timeout=30):
            return RdstResult(False, "PostgreSQL did not become ready within 30 seconds.")

        # Enable pg_stat_statements for rdst top
        subprocess.run([
            "docker", "exec", DEMO_CONTAINER,
            "psql", "-U", DEMO_USER, "-d", DEMO_DATABASE,
            "-c", "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;",
        ], capture_output=True)

        self.console.print(MessagePanel("Demo database is ready", variant="success"))
        self._print_connection_info()
        self.console.print(NextSteps([("rdst demo load", "Download and load demo data")]))
        return RdstResult(True)

    # ------------------------------------------------------------------
    # load
    # ------------------------------------------------------------------

    def _load(self, **_kwargs) -> RdstResult:
        """Download and load DBA StackExchange data with guided walkthrough."""
        status = self._container_status()
        if status != "running":
            return RdstResult(False, "Demo container is not running. Run 'rdst demo setup' first.")

        os.environ["RDST_DEMO_PASSWORD"] = DEMO_PASSWORD
        db_url = f"postgresql://{DEMO_USER}:{DEMO_PASSWORD}@127.0.0.1:{DEMO_PORT}/{DEMO_DATABASE}"
        total_steps = 6

        self._tour_header("Loading Demo Data",
            "We'll download a real dataset, load it into PostgreSQL, and set up "
            "rdst's semantic layer — the metadata that makes everything smarter")

        # Step 1: Download
        self._tour_step(1, total_steps, "Download the DBA StackExchange dataset",
            f"This is real data from dba.stackexchange.com — the Q&A site for database "
            f"professionals. It's about 300MB compressed and contains ~2M rows across "
            f"7 tables: posts, users, comments, votes, tags, badges, and postlinks.",
            None)

        try:
            import logging
            from lib.data.load_stackoverflow import (
                TABLES, DEFAULT_TABLES, PG_DDL,
                is_split_site, download_combined_archive, download_table_archive,
                load_pg, create_connection, parse_database_url,
            )
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
                datefmt="%H:%M:%S",
            )

            engine, params = parse_database_url(db_url)
            from pathlib import Path
            download_dir = Path.home() / ".rdst" / "cache" / "stackexchange"
            download_dir.mkdir(parents=True, exist_ok=True)

            split = is_split_site(DEMO_SITE, download_dir)
            if split:
                archive_map = {}
                for table in DEFAULT_TABLES:
                    path = download_table_archive(DEMO_SITE, TABLES[table], download_dir)
                    if path:
                        archive_map[table] = path
            else:
                combined = download_combined_archive(DEMO_SITE, download_dir)
                if not combined:
                    return RdstResult(False, "Failed to download archive.")
                archive_map = {t: combined for t in DEFAULT_TABLES}

            self.console.print(f"  [{StyleTokens.SUCCESS}]Download complete[/{StyleTokens.SUCCESS}]")
        except Exception as e:
            return RdstResult(False, f"Download failed: {e}")

        # Step 2: Load into PostgreSQL
        self._tour_step(2, total_steps, "Load data into PostgreSQL",
            "Now we'll extract the data and bulk-load it into the demo database. "
            "Each table is loaded using PostgreSQL's COPY command for speed.",
            None)

        try:
            conn = create_connection(engine, params)
            cur = conn.cursor()
            for table in DEFAULT_TABLES:
                if table not in archive_map:
                    continue
                cur.execute(f"DROP TABLE IF EXISTS {table}")
                cur.execute(PG_DDL[table])
            cur.close()

            for table in DEFAULT_TABLES:
                if table not in archive_map:
                    continue
                table_def = TABLES[table]
                row_count = load_pg(
                    conn, table, table_def, archive_map[table],
                    download_dir, None, False,
                )
                self.console.print(f"  [{StyleTokens.SUCCESS}]✓[/{StyleTokens.SUCCESS}] {table}: {row_count:,} rows")

            conn.close()
        except Exception as e:
            return RdstResult(False, f"Data loading failed: {e}")

        # Step 3: Configure target
        self._tour_step(3, total_steps, "Configure the database target",
            "RDST uses 'targets' to store connection profiles. We'll create a target "
            "called 'demo' that points to our Docker PostgreSQL. This is what you'd "
            "normally do with 'rdst configure add'.",
            None)

        self._configure_target()
        self.console.print(f"  [{StyleTokens.SUCCESS}]✓[/{StyleTokens.SUCCESS}] Target '{DEMO_TARGET}' configured")

        # Step 4: Schema init
        self._tour_step(4, total_steps, "Initialize the semantic layer",
            "The semantic layer stores metadata about your schema — table descriptions, "
            "column types, enum values, and relationships. RDST introspects your database "
            "to discover all of this automatically.",
            ["schema", "init", "--target", DEMO_TARGET, "--force"])

        # Step 5: Profile columns
        self._tour_step(5, total_steps, "Profile column statistics",
            "Now we collect statistics for each column — null fractions, distinct counts, "
            "and value distributions. These stats help 'rdst analyze' make smarter index "
            "recommendations by understanding data selectivity.",
            ["schema", "profile", "--target", DEMO_TARGET])

        # Step 6: AI annotation
        if os.environ.get("ANTHROPIC_API_KEY"):
            self._tour_step(6, total_steps, "Annotate schema with AI",
                "Finally, we use an LLM to generate human-readable descriptions for "
                "every table and column. This makes 'rdst ask' and 'rdst agent chat' "
                "much more effective because they understand what the data means.",
                ["schema", "annotate", "--target", DEMO_TARGET, "--use-llm", "--auto-accept"])
        else:
            self._tour_step(6, total_steps, "AI annotation (skipped)",
                "We'd normally use an LLM to generate descriptions for every table and "
                "column, but no ANTHROPIC_API_KEY is set. You can run this later with: "
                "rdst schema annotate --target demo --use-llm",
                None)

        self.console.print()
        self.console.print(MessagePanel("Demo data loaded and schema configured", variant="success"))
        self.console.print(NextSteps([("rdst demo tour", "Start the guided walkthrough")]))
        return RdstResult(True)

    # ------------------------------------------------------------------
    # tour
    # ------------------------------------------------------------------

    def _tour(self, tour_name: str = "quickstart", **_kwargs) -> RdstResult:
        """Run an interactive guided tour."""
        tours = {
            "quickstart": self._tour_quickstart,
            "schema": self._tour_schema,
            "analyze": self._tour_analyze,
            "ask": self._tour_ask,
            "top": self._tour_top,
            "chat": self._tour_chat,
        }

        if tour_name not in tours:
            # Show tour menu
            from lib.ui import SelectPrompt
            options = [
                "quickstart — 5-minute overview of key features",
                "schema — Explore and annotate database schema",
                "analyze — Analyze query performance",
                "ask — Natural language queries",
                "top — Live slow query monitoring",
                "chat — Conversational data exploration",
            ]
            try:
                choice = SelectPrompt.ask("Choose a tour:", options, default=1)
            except (EOFError, KeyboardInterrupt):
                return RdstResult(False, "Cancelled")
            if choice is None:
                return RdstResult(False, "Cancelled")
            tour_name = ["quickstart", "schema", "analyze", "ask", "top", "chat"][choice - 1]

        os.environ["RDST_DEMO_PASSWORD"] = DEMO_PASSWORD
        return tours[tour_name]()

    def _tour_quickstart(self) -> RdstResult:
        """5-minute overview of key features."""
        self._tour_header("Quickstart Tour", "A 5-minute overview of rdst's key features")

        self._tour_step(1, 5, "What's in this database?",
            "Let's start by looking at the schema. RDST's semantic layer stores "
            "descriptions, column stats, and relationships.",
            ["schema", "show", "--target", DEMO_TARGET])

        self._tour_step(2, 5, "Ask a question in plain English",
            "RDST converts natural language to SQL and runs it against your database.",
            ["ask", "How many users are there?", "--target", DEMO_TARGET, "--no-interactive"])

        self._tour_step(3, 5, "Analyze a query's performance",
            "RDST runs EXPLAIN ANALYZE, collects schema context including column "
            "selectivity stats, and uses an LLM to identify bottlenecks and suggest indexes.",
            ["analyze", "-q",
             "SELECT title, score FROM posts WHERE posttypeid = 1 ORDER BY score DESC LIMIT 20",
             "--target", DEMO_TARGET])

        self._tour_step(4, 5, "Explore column statistics",
            "The semantic layer stores null fractions and distinct counts per column. "
            "This helps the analyzer avoid false positive index recommendations.",
            ["schema", "show", "--target", DEMO_TARGET, "posts"])

        self._tour_step(5, 6, "What's next?",
            "You've seen the basics! Try these tours to go deeper:",
            None)
        self.console.print(NextSteps([
            ("rdst demo tour schema", "Deep dive into schema features"),
            ("rdst demo tour analyze", "Query performance analysis"),
            ("rdst demo tour ask", "Natural language queries"),
            ("rdst demo tour top", "Live slow query monitoring"),
            ("rdst demo tour chat", "Conversational data exploration"),
            ("rdst demo teardown", "Clean up when done"),
        ]))

        return RdstResult(True)

    def _tour_schema(self) -> RdstResult:
        self._tour_header("Schema Tour", "Explore and understand your database schema")

        self._tour_step(1, 3, "View the schema overview",
            "The semantic layer shows all tables with descriptions, types, and enum values.",
            ["schema", "show", "--target", DEMO_TARGET])

        self._tour_step(2, 3, "Column-level statistics",
            "After running 'rdst schema profile', each column shows null fraction and "
            "distinct count. These stats help the analyzer make better index recommendations.",
            ["schema", "show", "--target", DEMO_TARGET, "badges"])

        self._tour_step(3, 3, "Profile a table",
            "The profile command collects fresh statistics from the database.",
            ["schema", "profile", "--target", DEMO_TARGET])

        return RdstResult(True)

    def _tour_analyze(self) -> RdstResult:
        self._tour_header("Analyze Tour", "Query performance analysis with selectivity awareness")

        self._tour_step(1, 3, "A query that needs an index",
            "This query filters on score (high cardinality). The analyzer should "
            "recommend a useful index.",
            ["analyze", "-q",
             "SELECT title, score FROM posts WHERE score > 100 ORDER BY score DESC",
             "--target", DEMO_TARGET, "--skip-warning"])

        self._tour_step(2, 3, "A query where an index won't help",
            "This query filters on posttypeid which has only ~6 distinct values. "
            "With selectivity stats, the analyzer knows an index provides minimal benefit.",
            ["analyze", "-q",
             "SELECT title FROM posts WHERE posttypeid = 2",
             "--target", DEMO_TARGET, "--skip-warning"])

        self._tour_step(3, 3, "A join query",
            "Multi-table queries get full analysis including join efficiency.",
            ["analyze", "-q",
             "SELECT p.title, u.displayname, p.score FROM posts p JOIN users u ON p.owneruserid = u.id WHERE p.score > 50 ORDER BY p.score DESC LIMIT 20",
             "--target", DEMO_TARGET, "--skip-warning"])

        return RdstResult(True)

    def _tour_ask(self) -> RdstResult:
        self._tour_header("Ask Tour", "Query your database in plain English")

        self._tour_step(1, 3, "A basic question",
            "RDST generates SQL from your question and runs it.",
            ["ask", "What are the most popular tags?", "--target", DEMO_TARGET, "--no-interactive"])

        self._tour_step(2, 3, "A question involving joins",
            "It handles multi-table queries automatically.",
            ["ask", "Who posted the most answers?", "--target", DEMO_TARGET, "--no-interactive"])

        self._tour_step(3, 3, "An aggregation question",
            "Grouping and aggregation work naturally.",
            ["ask", "What is the average score by post type?", "--target", DEMO_TARGET, "--no-interactive"])

        return RdstResult(True)

    def _tour_top(self) -> RdstResult:
        self._tour_header("Top Tour", "Live slow query monitoring")

        self._tour_step(1, 3, "Save demo queries for load generation",
            "We'll save a mix of fast, medium, and slow queries to the registry, "
            "then run them as background traffic so 'rdst top' has something to show.",
            None)

        # Save demo queries
        for name, sql in DEMO_QUERIES.items():
            cmd = f'rdst query add {name} -q "{sql}" --target {DEMO_TARGET}'
            self.console.print(f"  [{StyleTokens.COMMAND}]$ {cmd}[/{StyleTokens.COMMAND}]")
            self._run_rdst_quiet(["query", "add", name, "-q", sql, "--target", DEMO_TARGET])
            self.console.print(f"  [{StyleTokens.SUCCESS}]✓[/{StyleTokens.SUCCESS}] saved\n")

        try:
            input("\nPress Enter to continue... ")
        except (EOFError, KeyboardInterrupt):
            pass

        self._tour_step(2, 3, "Start background traffic",
            "Running demo queries in the background at 200ms intervals for 60 seconds. "
            "This generates the query traffic that 'rdst top' will monitor.",
            None)

        # Start background traffic
        import sys
        bg_process = subprocess.Popen(
            [sys.executable, "rdst.py", "query", "run"] + list(DEMO_QUERIES.keys()) +
            ["--target", DEMO_TARGET, "--duration", "60", "--interval", "200", "--quiet"],
            cwd=self._find_src_dir(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.console.print(f"  [{StyleTokens.SUCCESS}]Background traffic started (PID {bg_process.pid})[/{StyleTokens.SUCCESS}]")
        self.console.print(f"  [{StyleTokens.MUTED}]Waiting 5 seconds for queries to accumulate...[/{StyleTokens.MUTED}]")
        time.sleep(5)

        self._tour_step(3, 3, "Monitor with rdst top",
            "Now let's watch the live query traffic. Press Ctrl-C to exit when done.",
            ["top", "--target", DEMO_TARGET])

        # Clean up background process
        bg_process.terminate()
        try:
            bg_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bg_process.kill()

        return RdstResult(True)

    def _tour_chat(self) -> RdstResult:
        self._tour_header("Chat Tour", "Conversational data exploration")

        self.console.print(f"\n[{StyleTokens.EMPHASIS}]Chat vs Ask[/{StyleTokens.EMPHASIS}]")
        self.console.print(
            f"[{StyleTokens.MUTED}]'rdst ask' generates a single query. "
            f"'rdst agent chat' maintains a conversation — it can run multiple queries, "
            f"follow threads, and build on previous results.[/{StyleTokens.MUTED}]\n"
        )

        # Create agent if it doesn't exist (silently)
        self._run_rdst_quiet(["agent", "create", "--name", "demo-agent", "--target", DEMO_TARGET,
             "--description", "Demo chat agent for DBA StackExchange data"])

        self._tour_step(1, 1, "Start chatting",
            "Now let's chat with the agent. Ask questions about the data — it can run "
            "multiple queries, follow threads, and build on previous results. "
            "Type 'exit' to end.",
            ["agent", "chat", "--name", "demo-agent"])

        return RdstResult(True)

    # ------------------------------------------------------------------
    # teardown
    # ------------------------------------------------------------------

    def _teardown(self, **_kwargs) -> RdstResult:
        """Stop and remove demo container and target config."""
        status = self._container_status()
        if status == "not_found":
            self.console.print(MessagePanel("No demo container found", variant="warning"))
        else:
            self.console.print(f"[{StyleTokens.MUTED}]Stopping and removing demo container...[/{StyleTokens.MUTED}]")
            subprocess.run(["docker", "rm", "-f", DEMO_CONTAINER], capture_output=True)
            self.console.print(MessagePanel("Demo container removed", variant="success"))

        # Remove target from config
        try:
            from lib.cli.rdst_cli import TargetsConfig
            config = TargetsConfig()
            config.load()
            if config.get(DEMO_TARGET):
                config.remove(DEMO_TARGET)
                config.save()
                self.console.print(f"[{StyleTokens.MUTED}]Removed '{DEMO_TARGET}' target from config[/{StyleTokens.MUTED}]")
        except Exception:
            pass

        return RdstResult(True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _docker_available(self) -> bool:
        try:
            result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _container_status(self) -> str:
        """Return 'running', 'stopped', or 'not_found'."""
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Status}}", DEMO_CONTAINER],
                capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                return "not_found"
            status = result.stdout.strip()
            return "running" if status == "running" else "stopped"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return "not_found"

    def _wait_for_pg(self, timeout: int = 30) -> bool:
        """Wait for PostgreSQL to accept connections."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["docker", "exec", DEMO_CONTAINER, "pg_isready", "-U", DEMO_USER],
                capture_output=True, timeout=5)
            if result.returncode == 0:
                return True
            time.sleep(1)
        return False

    def _configure_target(self):
        """Add demo target to rdst config."""
        from lib.cli.rdst_cli import TargetsConfig
        config = TargetsConfig()
        config.load()
        config.upsert(DEMO_TARGET, {
            "name": DEMO_TARGET,
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": DEMO_PORT,
            "user": DEMO_USER,
            "database": DEMO_DATABASE,
            "password_env": "RDST_DEMO_PASSWORD",
            "read_only": False,
            "proxy": "none",
            "tls": False,
        })
        config.save()

    def _run_rdst(self, args: list[str]):
        """Run an rdst subcommand via the same entry point."""
        import sys
        subprocess.run(
            [sys.executable, "rdst.py"] + args,
            cwd=self._find_src_dir(),
        )

    def _run_rdst_quiet(self, args: list[str]):
        """Run an rdst subcommand silently."""
        import sys
        subprocess.run(
            [sys.executable, "rdst.py"] + args,
            cwd=self._find_src_dir(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _run_rdst_interactive(self, args: list[str]):
        """Run an rdst subcommand interactively (inherits stdin/stdout)."""
        import sys
        subprocess.run(
            [sys.executable, "rdst.py"] + args,
            cwd=self._find_src_dir(),
        )

    def _find_src_dir(self) -> str:
        """Find the src directory containing rdst.py."""
        from pathlib import Path
        # Try relative to this file
        this_dir = Path(__file__).resolve().parent
        src_dir = this_dir.parent.parent  # lib/cli -> lib -> src
        if (src_dir / "rdst.py").exists():
            return str(src_dir)
        return "."

    def _print_connection_info(self):
        self.console.print(f"  [{StyleTokens.MUTED}]Host: 127.0.0.1:{DEMO_PORT}[/{StyleTokens.MUTED}]")
        self.console.print(f"  [{StyleTokens.MUTED}]User: {DEMO_USER}  Database: {DEMO_DATABASE}[/{StyleTokens.MUTED}]")
        self.console.print(f"  [{StyleTokens.MUTED}]Password: {DEMO_PASSWORD}[/{StyleTokens.MUTED}]")

    def _tour_header(self, title: str, subtitle: str):
        self.console.print()
        self.console.print(f"[{StyleTokens.HEADER}]{title}[/{StyleTokens.HEADER}]")
        self.console.print(f"[{StyleTokens.MUTED}]{subtitle}[/{StyleTokens.MUTED}]")
        self.console.print(f"[{StyleTokens.MUTED}]Press Enter to advance, 'q' to quit[/{StyleTokens.MUTED}]")

    def _tour_step(self, step: int, total: int, title: str, explanation: str,
                   command: list[str] | None):
        self.console.print(f"\n[{StyleTokens.EMPHASIS}]Step {step}/{total}: {title}[/{StyleTokens.EMPHASIS}]")
        self.console.print(f"[{StyleTokens.MUTED}]{explanation}[/{StyleTokens.MUTED}]")

        if command:
            cmd_str = "rdst " + " ".join(
                f'"{a}"' if " " in a else a for a in command
            )
            self.console.print(f"\n  [{StyleTokens.COMMAND}]$ {cmd_str}[/{StyleTokens.COMMAND}]\n")

            try:
                response = input("Press Enter to run (or 'q' to quit)... ")
                if response.strip().lower() == "q":
                    self.console.print(f"[{StyleTokens.MUTED}]Tour ended.[/{StyleTokens.MUTED}]")
                    raise SystemExit(0)
            except (EOFError, KeyboardInterrupt):
                return

            os.environ["RDST_DEMO_PASSWORD"] = DEMO_PASSWORD
            self._run_rdst(command)
        else:
            # Info-only step, no command
            pass

        if step < total:
            try:
                input("\nPress Enter to continue... ")
            except (EOFError, KeyboardInterrupt):
                pass
