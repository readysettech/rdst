"""
Modern interactive configuration wizard for rdst CLI.
Handles database target configuration with a beautiful, step-by-step interface.
"""

from typing import List, Dict, Optional, Any
from .rdst_cli import RdstResult, TargetsConfig, normalize_db_type, default_port_for, ENGINES, PROXY_TYPES

try:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    from rich.prompt import Prompt, Confirm, IntPrompt
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False


class ConfigurationWizard:
    """Modern interactive configuration wizard with beautiful UI."""

    def __init__(self, console=None):
        self.console = console if console and _RICH_AVAILABLE else (Console() if _RICH_AVAILABLE else None)
        self._has_rich = _RICH_AVAILABLE and self.console is not None

    def configure_targets(self, subcmd: str, cfg: TargetsConfig, **kwargs) -> RdstResult:
        """Main entry point for target configuration."""
        handlers = {
            "list": self._list_targets,
            "remove": self._remove_target,
            "default": self._set_default_target,
            "add": lambda cfg, kwargs: self._add_edit_target(cfg, "add", kwargs),
            "edit": lambda cfg, kwargs: self._add_edit_target(cfg, "edit", kwargs),
            "menu": self._menu,
        }

        if subcmd not in handlers:
            return RdstResult(False, f"Unknown subcommand: {subcmd}")

        return handlers[subcmd](cfg, kwargs)

    def _list_targets(self, cfg: TargetsConfig,  kwargs: dict, show_tips: bool = False) -> RdstResult:
        """Display all configured targets with modern formatting."""
        targets = cfg.list_targets()
        default_name = cfg.get_default()

        if not targets:
            if self._has_rich:
                from rich.text import Text as _Text
                msg = _Text()
                msg.append("No database targets configured yet.\nRun ")
                msg.append("rdst configure add", style="bold cyan")
                msg.append(" to get started.")
                self._show_info("No Targets", msg)
            else:
                self._show_info("No Targets",
                    "No database targets configured yet.\nRun rdst configure add to get started.")
            return RdstResult(True, "No targets configured")

        if not self._has_rich:
            self._list_targets_basic(cfg, targets, default_name)
            return RdstResult(True, "Targets listed")

        # Clean table format - consistent with all other tables
        table = Table(
            title="Database Targets",
            show_header=True
        )

        table.add_column("Name", no_wrap=True)
        table.add_column("Engine")
        table.add_column("Connection")
        table.add_column("Proxy")
        table.add_column("Verified", justify="center")
        table.add_column("Status", justify="center")

        for name in targets:
            target = cfg.get(name) or {}

            # Format connection string
            conn_str = f"{target.get('host', 'unknown')}:{target.get('port', '?')}/{target.get('database', '?')}"

            # Status without icons
            status = Text("Default") if name == default_name else Text("Available")

            # Verified flag
            verified_flag = bool(target.get('endpoint_verified', target.get('verified', False)))
            verified_display = Text("Yes") if verified_flag else Text("No")

            # Engine without icons
            engine = target.get('engine', 'unknown')
            engine_display = {
                'postgresql': 'PostgreSQL',
                'mysql': 'MySQL'
            }.get(engine, engine)

            # Proxy without icons
            proxy = target.get('proxy', 'none')
            proxy_display = {
                'none': 'None',
                'readyset': 'Readyset',
                'proxysql': 'ProxySQL',
                'pgbouncer': 'PgBouncer',
                'tunnel': 'SSH Tunnel',
                'custom': 'Custom'
            }.get(proxy, proxy)

            table.add_row(name, engine_display, conn_str, proxy_display, verified_display, status)

        self.console.print(table)

        # Show helpful hints
        if show_tips:
            if self._has_rich:
                from rich.text import Text as _Text
                tips = _Text()
                tips.append("- Set default: ")
                tips.append("rdst configure default <name>", style="bold cyan")
                tips.append("\n- Edit target: ")
                tips.append("rdst configure edit <name>", style="bold cyan")
                tips.append("\n- Remove target: ")
                tips.append("rdst configure remove <name>", style="bold cyan")
                tips.append("\n- List target: ")
                tips.append("rdst configure list", style="bold cyan")
                self._show_info("Tips", tips)
            else:
                self._show_info("Tips",
                    "- Set default: rdst configure default <name>\n"
                    "- Edit target: rdst configure edit <name>\n"
                    "- Remove target: rdst configure remove <name>\n"
                    "- List target: rdst configure list"
                )

        return RdstResult(True, f"Found {len(targets)} configured targets")

    def _menu(self, cfg: TargetsConfig, kwargs: dict) -> RdstResult:
        """Main menu when running `rdst configure` without options.
        - If no targets exist: inform user and start the add flow.
        - If targets exist: list them and offer options to add, set default, or delete.
        """
        targets = cfg.list_targets()
        if not targets:
            # Inform and immediately start configuring the first target
            self._show_info("No Targets", "No targets configured yet. Let's add your first target.")
            res = self._add_edit_target(cfg, "add", kwargs)
            return res

        # Show current targets first
        self._list_targets(cfg, kwargs, True)

        # Offer simple choices per requirement
        choices = [
            "Add new target",
            "Edit a target",
            "Set default target",
            "Delete a target",
            "Exit"
        ]
        choice = self._interactive_select("Choose an action", choices, default_idx=0)
        if choice == choices[0]:
            # Add
            return self._add_edit_target(cfg, "add", kwargs)
        if choice == choices[1]:
            # Edit
            return self._add_edit_target(cfg, "edit", kwargs)
        if choice == choices[2]:
            # Set default
            name = self._interactive_select("Select target to set as default", targets, default_idx=0)
            if not name:
                return RdstResult(False, "Selection cancelled")
            cfg.set_default(name)
            cfg.save()
            self._show_success("Default Set", f"'{name}' is now the default target")
            return RdstResult(True, f"Default target set to '{name}'")
        if choice == choices[3]:
            # Delete
            return self._remove_target(cfg, kwargs)
        # Exit or cancelled
        return RdstResult(True, "No action selected")

    def _list_targets_basic(self, cfg: TargetsConfig, targets: List[str], default_name: str):
        """Basic target listing for terminals without Rich."""
        print("\nConfigured Database Targets:")
        print("-" * 40)

        for name in targets:
            target = cfg.get(name) or {}
            default_marker = " (default)" if name == default_name else ""
            engine = target.get('engine', 'unknown')
            host = target.get('host', 'unknown')
            port = target.get('port', '?')
            database = target.get('database', '?')

            print(f"• {name}{default_marker}")
            print(f"  {engine}://{host}:{port}/{database}")
            verified_flag = bool(target.get('endpoint_verified', target.get('verified', False)))
            print(f"  Verified: {'Yes' if verified_flag else 'No'}")

        print(f"\nTotal: {len(targets)} targets configured")

    def _remove_target(self, cfg: TargetsConfig, kwargs: dict) -> RdstResult:
        """Remove a target with confirmation."""
        name = kwargs.get("target") or kwargs.get("name")

        if not name:
            # Interactive selection
            targets = cfg.list_targets()
            if not targets:
                self._show_warning("No Targets", "No targets available to remove")
                return RdstResult(False, "No targets to remove")

            name = self._interactive_select("Select target to remove", targets)
            if not name:
                return RdstResult(False, "Selection cancelled")

        target = cfg.get(name)
        if not target:
            self._show_error("Not Found", f"Target '{name}' does not exist")
            return RdstResult(False, f"Target '{name}' not found")

        # Show target details before removal
        self._show_target_details(name, target, "Remove Target", "red")

        # Confirm removal
        if not kwargs.get("confirm"):
            confirmed = self._confirm_action(
                f"Are you sure you want to remove target '{name}'?",
                "This action cannot be undone!",
                danger=True
            )
            if not confirmed:
                self._show_info("Cancelled", "Target removal cancelled")
                return RdstResult(False, "Removal cancelled")

        # Remove target
        cfg.remove(name)
        cfg.save()

        self._show_success("Removed", f"Target '{name}' has been removed")
        return RdstResult(True, f"Target '{name}' removed")

    def _set_default_target(self, cfg: TargetsConfig, kwargs: dict) -> RdstResult:
        """Set the default target."""
        name = kwargs.get("target") or kwargs.get("name")
        targets = cfg.list_targets()

        if not targets:
            self._show_warning("No Targets", "No targets configured yet")
            return RdstResult(False, "No targets available")

        if not name:
            name = self._interactive_select("Select default target", targets)
            if not name:
                return RdstResult(False, "Selection cancelled")

        if not cfg.get(name):
            self._show_error("Not Found", f"Target '{name}' does not exist")
            return RdstResult(False, f"Target '{name}' not found")

        cfg.set_default(name)
        cfg.save()

        self._show_success("Default Set", f"'{name}' is now your default target")
        return RdstResult(True, f"Default target set to '{name}'")

    def _add_edit_target(self, cfg: TargetsConfig, subcmd: str, kwargs: dict) -> RdstResult:
        """Add or edit a target with modern wizard."""
        is_edit = subcmd == "edit"
        name = kwargs.get("target") or kwargs.get("name")

        # For edit, we need to select a target if not provided
        if is_edit and not name:
            targets = cfg.list_targets()
            if not targets:
                self._show_warning("No Targets", "No targets available to edit")
                return RdstResult(False, "No targets to edit")

            name = self._interactive_select("Select target to edit", targets)
            if not name:
                return RdstResult(False, "Selection cancelled")

        # Get existing config for edit
        existing = cfg.get(name) if (is_edit and name) else {}

        # Check if we have enough info from CLI args
        has_connection_string = bool(kwargs.get("connection_string") or kwargs.get("connection-string"))
        has_required_args = all(kwargs.get(field) for field in ["host", "user", "database"])

        if (has_required_args or has_connection_string) and not is_edit:
            # Quick CLI mode - either individual args or connection string provided
            config_data = self._collect_config_from_args(kwargs, existing, name)
        else:
            # Interactive wizard mode
            config_data = self._run_configuration_wizard(existing, is_edit, name)

        if not config_data:
            self._show_info("Cancelled", "Configuration cancelled")
            return RdstResult(False, "Configuration cancelled")

        # Validate configuration
        validation_result = self._validate_config(config_data)
        if not validation_result.ok:
            return validation_result

        # Test connection (skip if --skip-verify flag is set)
        skip_verify = kwargs.get("skip_verify") or kwargs.get("skip-verify", False)
        if skip_verify:
            # Skip connection verification entirely (for MCP/non-interactive use)
            config_data["verified"] = False
            config_data["endpoint_verified"] = False
            self._show_info("Skipped", "Connection verification skipped (--skip-verify)")
        else:
            test_result = self._test_connection(config_data)
            if test_result.ok:
                self._show_success("Connection Test", test_result.message)
                config_data["verified"] = True
                config_data["endpoint_verified"] = True
            else:
                self._show_error("Connection Test Failed", test_result.message)
                config_data["verified"] = False
                config_data["endpoint_verified"] = False

                target_name = name or config_data.get("name", "target")
                # Ask if they want to save anyway
                if not self._confirm(
                    "Save configuration anyway?",
                    f"You can edit later with: rdst configure edit {target_name}",
                    default=False
                ):
                    return RdstResult(False, "Configuration cancelled due to connection failure")

        # Save configuration
        target_name = name or config_data["name"]
        # Preserve existing flags like verification when editing
        if existing:
            merged = dict(existing)
            merged.update(config_data)
            cfg.upsert(target_name, merged)
        else:
            cfg.upsert(target_name, config_data)

        if config_data.get("make_default"):
            cfg.set_default(target_name)

        cfg.save()

        # Show success
        action = "updated" if is_edit else "added"
        self._show_success(
            f"Target '{target_name}'",
            f"has been {action} successfully!"
        )

        # Breadcrumb: show next steps with colors
        # Colors: rdst=white, subcommand=green, values/quoted=blue, descriptions=dim
        if not is_edit:
            if self._has_rich:
                self.console.print()
                self.console.print("[cyan]Next Steps:[/cyan]")
                self.console.print(f"  rdst [green]top[/green] --target [blue]{target_name}[/blue]              [dim]Monitor slow queries[/dim]")
                self.console.print(f"  rdst [green]analyze[/green] -q [blue]\"SELECT ...\"[/blue] --target [blue]{target_name}[/blue]   [dim]Analyze a query[/dim]")
                self.console.print(f"  rdst [green]configure test[/green] [blue]{target_name}[/blue]            [dim]Test connection[/dim]")
            else:
                self._show_info(
                    "Next Steps",
                    f"  rdst top --target {target_name}              Monitor slow queries\n"
                    f"  rdst analyze -q \"SELECT ...\" --target {target_name}   Analyze a query\n"
                    f"  rdst configure test {target_name}            Test connection"
                )

        # Return empty message since _show_success already displayed status
        return RdstResult(True, "", data={
            "target": target_name,
            "config": config_data,
            "default": cfg.get_default()
        })

    def _run_configuration_wizard(self, existing: dict, is_edit: bool, name: str = None) -> Optional[dict]:
        """Run the interactive configuration wizard."""

        if not self._has_rich:
            return self._basic_interactive_config(existing, is_edit, name)

        # Welcome message with compact panel
        action = "Edit" if is_edit else "Create"
        welcome_text = f"Let's {'update' if is_edit else 'configure'} your database connection!"

        if is_edit and name:
            welcome_text += f"\n\nEditing target: [bold cyan]{name}[/bold cyan]"

        # Simplified welcome message without decorative panel/effects
        self.console.print(f"{action} Database Target")
        self.console.print(welcome_text)

        try:
            config = {}

            # Step 1: Basic Info
            self._show_step("Step 1", "Basic Information", "")
            config["name"] = name or self._prompt_text(
                "Target name",
                "A friendly name for this connection (e.g., prod, staging, dev)",
                existing.get("name"),
                required=True
            )

            # Database Engine - combined with step info
            config["engine"] = self._select_database_engine_with_step(existing.get("engine", "postgresql"))

            # Step 3: Connection Details
            self._show_step("Step 3", "Connection Details", "🔌")
            config.update(self._collect_connection_details(config["engine"], existing))

            # Step 4: Security & Authentication
            self._show_step("Step 4", "Security & Authentication", "🔐")
            config.update(self._collect_security_settings(config["name"], existing))

            # Step 5: Advanced Options
            self._show_step("Step 5", "Advanced Configuration", "")
            config.update(self._collect_advanced_settings(existing, config.get("engine")))

            # Step 6: Finalization
            self._show_step("Step 6", "Finalization", "")
            config["make_default"] = self._confirm(
                "Set as default target?",
                "Use this target when no specific target is specified",
                False
            )

            # Show configuration summary
            self._show_config_summary(config)

            if not self._confirm("Save this configuration?", "Create the database target", True):
                return None

            return config

        except (EOFError, KeyboardInterrupt):
            self._show_info("Cancelled", "Configuration wizard cancelled")
            return None

    def _select_database_engine_with_step(self, current_engine: str) -> str:
        """Select database engine with integrated step information."""
        engines = ["PostgreSQL", "MySQL"]
        default_idx = 0 if current_engine == "postgresql" else 1

        if not self._has_rich:
            print("\n=== Step 2: Database Engine ===")
            print("Supported database engines:")
            for i, engine in enumerate(engines):
                marker = "*" if i == default_idx else " "
                print(f" {marker} [{i + 1}] {engine}")

            try:
                response = input(f"Select engine [{default_idx + 1}]: ").strip()
                if not response:
                    return engines[default_idx].lower()
                idx = int(response) - 1
                return engines[idx].lower() if 0 <= idx < len(engines) else engines[default_idx].lower()
            except (ValueError, IndexError):
                return engines[default_idx].lower()

        # Combined step header and selection (simplified)
        self.console.print()
        self.console.print("Step 2: Database Engine")
        self.console.print("Select your database engine")

        engine_choice = self._interactive_select(
            "Choose database engine",
            engines,
            default_idx
        )

        return engine_choice.lower() if engine_choice else "postgresql"

    def _select_database_engine(self, current_engine: str) -> str:
        """Select database engine with simplified interface."""
        engines = ["PostgreSQL", "MySQL"]
        default_idx = 0 if current_engine == "postgresql" else 1

        if not self._has_rich:
            print("\nSupported database engines:")
            for i, engine in enumerate(engines):
                marker = "*" if i == default_idx else " "
                print(f" {marker} [{i+1}] {engine}")

            try:
                response = input(f"Select engine [{default_idx+1}]: ").strip()
                if not response:
                    return engines[default_idx].lower()
                idx = int(response) - 1
                return engines[idx].lower() if 0 <= idx < len(engines) else engines[default_idx].lower()
            except (ValueError, IndexError):
                return engines[default_idx].lower()

        # Simple selection without preview
        engine_choice = self._interactive_select(
            "Select database engine",
            engines,
            default_idx
        )

        return engine_choice.lower() if engine_choice else "postgresql"

    def _collect_connection_details(self, engine: str, existing: dict) -> dict:
        """Collect database connection details."""
        config = {}

        config["host"] = self._prompt_text(
            "Database host",
            "Hostname or IP address of your database server",
            existing.get("host") or "localhost",
            required=True
        )

        default_port = default_port_for(engine)
        port_input = self._prompt_text(
            "Port",
            f"Database port number (default: {default_port})",
            str(existing.get("port", default_port))
        )
        config["port"] = int(port_input) if port_input else default_port

        config["database"] = self._prompt_text(
            "Database name",
            "Name of the database to connect to",
            existing.get("database"),
            required=True
        )

        config["user"] = self._prompt_text(
            "Username",
            "Database username for authentication",
            existing.get("user"),
            required=True
        )

        return config

    def _collect_security_settings(self, target_name: str, existing: dict) -> dict:
        """Collect security and authentication settings."""
        config = {}

        # Password handling (simplified)
        password_options = [
            "Environment variable (recommended)",
            "Skip password configuration"
        ]

        password_previews = None

        password_choice = self._interactive_select_with_preview(
            "How should we handle the password?",
            password_options,
            0,
            password_previews
        )

        if "Environment variable" in password_choice:
            suggested_var = f"{target_name.upper().replace('-', '_')}_PASSWORD"
            config["password_env"] = self._prompt_text(
                "Environment variable name",
                "Name of the environment variable containing the password",
                existing.get("password_env") or suggested_var
            )
        else:
            config["password_env"] = ""

        # TLS Configuration
        config["tls"] = self._confirm(
            "Enable TLS/SSL encryption?",
            "Encrypts communication with the database",
            existing.get("tls", True)
        )

        return config

    def _allowed_proxies_for_engine(self, engine: Optional[str]) -> List[str]:
        """Return allowed proxy types for a given engine.
        Defaults to conservative set if engine unknown.
        """
        eng = (engine or "").lower()
        if eng == "postgresql":
            return ["none", "readyset", "pgbouncer", "tunnel", "custom"]
        if eng == "mysql":
            return ["none", "readyset", "proxysql", "tunnel", "custom"]
        # Fallback: allow common types
        return ["none", "readyset", "tunnel", "custom"]

    def _collect_advanced_settings(self, existing: dict, engine: Optional[str] = None) -> dict:
        """Collect advanced configuration settings.
        Shows only relevant proxy types based on the SQL engine.
        """
        config = {}

        # Build engine-aware proxy list
        allowed_values = self._allowed_proxies_for_engine(engine or existing.get("engine"))
        labels = {
            "none": "None - Direct connection",
            "readyset": "Readyset - Caching proxy",
            "proxysql": "ProxySQL - Connection pooling",
            "pgbouncer": "PgBouncer - PostgreSQL pooling",
            "tunnel": "SSH Tunnel - Secure connection",
            "custom": "Custom proxy",
        }
        proxy_values = allowed_values
        proxy_options = [labels[v] for v in proxy_values]

        proxy_previews = None

        current_proxy = (existing.get("proxy") or "none").lower()
        default_proxy_idx = proxy_values.index(current_proxy) if current_proxy in proxy_values else 0

        proxy_choice = self._interactive_select_with_preview(
            "Select proxy type",
            proxy_options,
            default_proxy_idx,
            proxy_previews
        )

        # Map selection back to value
        if proxy_choice is None:
            chosen_value = proxy_values[default_proxy_idx]
        else:
            idx = proxy_options.index(proxy_choice)
            chosen_value = proxy_values[idx]
        config["proxy"] = chosen_value

        config["read_only"] = self._confirm(
            "Read-only connection?",
            "Restricts connection to SELECT queries only",
            existing.get("read_only", False)
        )

        return config

    # UI Helper Methods
    def _show_step(self, step: str, title: str, emoji: str):
        """Show a wizard step header."""
        if self._has_rich:
            self.console.print()
            self.console.print(f"{step}: {title}")
        else:
            print(f"\n=== {step}: {title} ===")

    def _prompt_text(self, label: str, description: str, default: str = None, required: bool = False) -> str:
        """Modern text prompt with rich formatting."""
        if not self._has_rich:
            prompt = f"{label}"
            if default:
                prompt += f" [{default}]"
            prompt += ": "
            response = input(prompt).strip()
            return response if response else (default or "")

        # Show description
        if description:
            desc_text = Text(description)
            self.console.print(desc_text)

        return Prompt.ask(
            f"[bold cyan]{label}[/bold cyan]",
            default=default,
            show_default=bool(default)
        )

    def _prompt(self, label: str, default: str = "") -> str:
        """Simple text prompt for user input."""
        if not self._has_rich:
            prompt = f"{label}"
            if default:
                prompt += f" [{default}]"
            prompt += ": "
            response = input(prompt).strip()
            return response if response else default

        return Prompt.ask(
            f"[bold cyan]{label}[/bold cyan]",
            default=default if default else None,
            show_default=bool(default)
        ) or ""

    def _confirm(self, question: str, description: str = None, default: bool = True) -> bool:
        """Modern confirmation prompt."""
        if not self._has_rich:
            suffix = " [Y/n]" if default else " [y/N]"
            response = input(f"{question}{suffix}: ").strip().lower()
            return response in ("y", "yes") if response else default

        if description:
            desc_text = Text(description)
            self.console.print(desc_text)

        return Confirm.ask(f"[bold cyan]{question}[/bold cyan]", default=default)

    def _interactive_select(self, prompt: str, choices: List[str], default_idx: int = 0) -> Optional[str]:
        """Interactive selection with modern UI."""
        if not self._has_rich:
            print(f"\n{prompt}:")
            for i, choice in enumerate(choices):
                print(f"  [{i+1}] {choice}")

            try:
                response = input(f"> [{default_idx+1}]: ").strip()
                if not response:
                    return choices[default_idx]
                idx = int(response) - 1
                return choices[idx] if 0 <= idx < len(choices) else choices[default_idx]
            except (ValueError, IndexError, EOFError, KeyboardInterrupt):
                return None

        # Clean selection table - consistent formatting
        table = Table(show_header=False, show_edge=False, padding=(0, 2))
        table.add_column("Choice", style="bold white")
        table.add_column("Option", style="cyan")

        for i, choice in enumerate(choices):

            table.add_row(f"[{i+1}]", choice)

        self.console.print(f"\n[bold cyan]{prompt}[/bold cyan]")
        self.console.print(table)

        try:
            choice_idx = IntPrompt.ask(
                "Select option",
                default=default_idx + 1,
                choices=[str(i+1) for i in range(len(choices))]
            )
            return choices[choice_idx - 1]
        except (EOFError, KeyboardInterrupt):
            return None

    def _interactive_select_with_preview(self, prompt: str, choices: List[str],
                                       default_idx: int = 0, previews: List[str] = None) -> Optional[str]:
        """Interactive selection with preview descriptions."""
        # Always use simplified selection without previews to keep UI consistent
        return self._interactive_select(prompt, choices, default_idx)

    def _confirm_action(self, question: str, warning: str = None, danger: bool = False) -> bool:
        """Confirm a potentially destructive action."""
        if not self._has_rich:
            if warning:
                print(f"WARNING: {warning}")
            response = input(f"{question} [y/N]: ").strip().lower()
            return response == "y"

        if warning:
            self.console.print(warning)

        return Confirm.ask(f"{question}", default=False)

    def _show_target_details(self, name: str, target: dict, title: str, style: str):
        """Show target details in a panel."""
        if not self._has_rich:
            print(f"\nTarget: {name}")
            print(f"Engine: {target.get('engine', 'unknown')}")
            print(f"Host: {target.get('host', 'unknown')}")
            print(f"Database: {target.get('database', 'unknown')}")
            verified_flag = bool(target.get('endpoint_verified', target.get('verified', False)))
            print(f"Verified: {'Yes' if verified_flag else 'No'}")
            return

        target_info = f"Engine: {target.get('engine', 'unknown')}\n"
        target_info += f"Host: {target.get('host', 'unknown')}\n"
        target_info += f"Database: {target.get('database', 'unknown')}\n"
        verified_flag = bool(target.get('endpoint_verified', target.get('verified', False)))
        target_info += f"Verified: {'Yes' if verified_flag else 'No'}"

        # Simplified target details output
        self.console.print(f"{title}: {name}")
        self.console.print(target_info)

    def _show_config_summary(self, config: dict):
        """Show a beautiful configuration summary."""
        if not self._has_rich:
            print("\nConfiguration Summary:")
            for key, value in config.items():
                if key != "make_default":
                    print(f"  {key}: {value}")
            return

        # Clean table format - consistent with database targets table
        table = Table(
            title="Configuration Summary", 
            show_header=True
        )
        
        table.add_column("Setting", style="bold cyan")
        table.add_column("Value", style="white")

        # Format values nicely
        display_map = {
            "name": config.get("name", ""),
            "engine": {"postgresql": "PostgreSQL", "mysql": "MySQL"}.get(config.get("engine", ""), config.get("engine", "")),
            "host": config.get("host", ""),
            "port": str(config.get("port", "")),
            "database": config.get("database", ""),
            "user": config.get("user", ""),
            "password_env": config.get("password_env", "") or "Not configured",
            "tls": "Enabled" if config.get("tls") else "Disabled",
            "proxy": config.get("proxy", "none").title(),
            "read_only": "Yes" if config.get("read_only") else "No"
        }

        for key, value in display_map.items():
            table.add_row(key.replace("_", " ").title(), value)

        # Display as actual table
        self.console.print()
        self.console.print(table)

    def _show_success(self, title: str, message: str):
        """Show success message (simplified)."""
        if self._has_rich:
            self.console.print(f"{title}: {message}")
        else:
            print(f"{title}: {message}")

    def _show_info(self, title: str, message):
        """Show info message (simplified). Accepts str or rich Text."""
        if self._has_rich:
            from rich.text import Text as _Text  # type: ignore
            if isinstance(message, _Text):
                self.console.print(f"{title}:")
                self.console.print(message)
            else:
                self.console.print(f"{title}:\n{message}")
        else:
            print(f"{title}: {message}")

    def _show_warning(self, title: str, message: str):
        """Show warning message (simplified)."""
        if self._has_rich:
            self.console.print(f"{title}: {message}")
        else:
            print(f"{title}: {message}")

    def _show_error(self, title: str, message: str):
        """Show error message (simplified)."""
        if self._has_rich:
            self.console.print(f"{title}: {message}")
        else:
            print(f"{title}: {message}")

    def _test_connection(self, config: dict) -> RdstResult:
        """Test database connection with the provided configuration."""
        import os

        engine = config.get("engine", "postgresql")
        host = config.get("host")
        port = config.get("port")
        user = config.get("user")
        database = config.get("database")
        password_env = config.get("password_env")
        tls = config.get("tls", False)

        # Get password from environment variable
        password = ""
        if password_env:
            password = os.environ.get(password_env, "")
            if not password:
                return RdstResult(
                    False,
                    f"Environment variable '{password_env}' is not set.\n"
                    f"Set it with: export {password_env}=\"your_password\""
                )

        self._show_info("Testing Connection", f"Connecting to {host}:{port}/{database}...")

        try:
            if engine == "postgresql":
                import psycopg2
                conn = psycopg2.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=10,
                    sslmode="require" if tls else "prefer"
                )
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                conn.close()
                return RdstResult(True, f"Connected successfully!\nServer: {version[:80]}...")

            elif engine == "mysql":
                import pymysql
                conn = pymysql.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=10,
                    ssl={"ssl": {}} if tls else None
                )
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                conn.close()
                return RdstResult(True, f"Connected successfully!\nServer: MySQL {version}")

            else:
                return RdstResult(False, f"Unknown engine: {engine}")

        except ImportError as e:
            driver = "psycopg2" if engine == "postgresql" else "pymysql"
            return RdstResult(
                False,
                f"Missing database driver: {driver}\n"
                f"Install with: pip install {driver}"
            )
        except Exception as e:
            error_msg = str(e)
            # Clean up common error messages
            if "could not connect" in error_msg.lower() or "connection refused" in error_msg.lower():
                return RdstResult(
                    False,
                    f"Connection refused: Cannot reach {host}:{port}\n"
                    f"Check that the database is running and accessible."
                )
            elif "authentication failed" in error_msg.lower() or "access denied" in error_msg.lower():
                return RdstResult(
                    False,
                    f"Authentication failed for user '{user}'\n"
                    f"Check your username and password."
                )
            elif "does not exist" in error_msg.lower():
                return RdstResult(
                    False,
                    f"Database '{database}' does not exist.\n"
                    f"Check the database name."
                )
            elif "timeout" in error_msg.lower():
                return RdstResult(
                    False,
                    f"Connection timed out to {host}:{port}\n"
                    f"Check network connectivity and firewall rules."
                )
            else:
                return RdstResult(False, f"Connection failed: {error_msg}")

    def _validate_config(self, config: dict) -> RdstResult:
        """Validate configuration with modern error reporting."""
        errors = []

        # Check required fields
        required_fields = {
            "name": "Target name",
            "host": "Database host",
            "user": "Username",
            "database": "Database name"
        }

        for field, display_name in required_fields.items():
            if not config.get(field):
                errors.append(f"Missing {display_name}")

        # Validate engine
        if config.get("engine") not in ENGINES:
            errors.append(f"Engine must be one of: {', '.join(ENGINES)}")

        # Validate port
        try:
            port = int(config.get("port", 0))
            if port <= 0 or port > 65535:
                errors.append("Port must be between 1 and 65535")
            config["port"] = port
        except (ValueError, TypeError):
            errors.append("Port must be a valid number")

        # Validate proxy (engine-specific)
        engine = config.get("engine")
        allowed_proxies = self._allowed_proxies_for_engine(engine)
        if config.get("proxy") not in allowed_proxies:
            errors.append(f"For engine '{engine}', proxy must be one of: {', '.join(allowed_proxies)}")

        if errors:
            # Create compact error display
            error_list = "\n".join([f"• {error}" for error in errors])
            error_content = f"Configuration validation failed:\n\n{error_list}"

            self._show_error("Validation Error", error_content)
            return RdstResult(False, error_content)
        
        return RdstResult(True, "Configuration is valid")

    def _collect_config_from_args(self, kwargs: dict, existing: dict, name: str = None) -> dict:
        """Collect configuration from command line arguments.

        If --connection-string is provided, parse it first and use individual flags to override.
        """
        from .rdst_cli import parse_connection_string

        parsed_values = {}

        # Parse connection string if provided
        connection_string = kwargs.get("connection_string") or kwargs.get("connection-string")
        password_from_connstring = None
        if connection_string:
            try:
                parsed_values = parse_connection_string(connection_string)
                # If password was in connection string, save it for env var prompt
                if parsed_values.get('password'):
                    password_from_connstring = parsed_values.get('password')
            except ValueError as e:
                # Show error but continue - validation will catch issues
                self._show_error("Connection String Error", str(e))
                return {}

        # Build config with priority: individual flags > connection string > existing > defaults
        # First, determine the target name (needed for password_env resolution)
        target_name = name or kwargs.get("target") or kwargs.get("name")

        config = {
            "name": target_name,
            "engine": normalize_db_type(
                kwargs.get("engine") or
                parsed_values.get("engine") or
                existing.get("engine")
            ) or "postgresql",
            "host": (
                kwargs.get("host") or
                parsed_values.get("host") or
                existing.get("host")
            ),
            "port": (
                kwargs.get("port") or
                parsed_values.get("port") or
                existing.get("port") or
                default_port_for(kwargs.get("engine") or parsed_values.get("engine", "postgresql"))
            ),
            "user": (
                kwargs.get("user") or
                parsed_values.get("user") or
                existing.get("user")
            ),
            "database": (
                kwargs.get("database") or
                parsed_values.get("database") or
                existing.get("database")
            ),
            "password_env": self._resolve_password_env(
                kwargs,
                existing,
                target_name,
                password_from_connstring
            ),
            "read_only": bool(
                kwargs.get("read_only") or
                kwargs.get("read-only") or
                existing.get("read_only", False)
            ),
            "proxy": (
                kwargs.get("proxy") or
                existing.get("proxy") or
                "none"
            ).lower(),
            "tls": self._resolve_tls_flag(kwargs, existing, parsed_values),
            "make_default": bool(kwargs.get("default"))
        }

        return config

    def _resolve_password_env(self, kwargs: dict, existing: dict, target_name: str, password_from_connstring: str = None) -> str:
        """Resolve password environment variable name.

        If password was in connection string, suggest env var and warn user to set it.
        Priority: --password-env flag > existing config > generated suggestion
        """
        # Check for explicit password-env flag
        explicit_env = kwargs.get("password_env") or kwargs.get("password-env")
        if explicit_env:
            return explicit_env

        # If existing config has password_env, use it
        if existing.get("password_env"):
            return existing["password_env"]

        # If password was in connection string, suggest env var name
        if password_from_connstring:
            suggested_var = f"{target_name.upper().replace('-', '_')}_PASSWORD" if target_name else "DB_PASSWORD"
            self._show_warning(
                "Password in Connection String",
                f"Found password in connection string.\n"
                f"For security, rdst stores passwords in environment variables.\n\n"
                f"Suggested: export {suggested_var}=\"{password_from_connstring}\"\n\n"
                f"You can override with --password-env flag."
            )
            return suggested_var

        return ""

    def _resolve_tls_flag(self, kwargs: dict, existing: dict, parsed_values: dict = None) -> bool:
        """Resolve TLS flag from various sources.

        Priority: --tls/--no-tls flags > connection string > existing config > default (False)
        """
        # Explicit --tls flag (only True matters, False is argparse default)
        if kwargs.get("tls"):
            return True

        # Explicit --no-tls flag
        if kwargs.get("no_tls") or kwargs.get("no-tls"):
            return False

        # Connection string TLS setting
        if parsed_values and "tls" in parsed_values:
            return bool(parsed_values["tls"])

        # Existing config default
        return bool(existing.get("tls", False))

    def _basic_interactive_config(self, existing: dict, is_edit: bool, name: str = None) -> Optional[dict]:
        """Fallback interactive config for terminals without Rich."""
        print(f"\n{'=== Edit Target ===' if is_edit else '=== Create Target ==='}")
        
        try:
            config = {}
            config["name"] = name or input(f"Target name [{existing.get('name', '')}]: ").strip() or existing.get("name", "")
            
            print("\nDatabase engines:")
            print("  [1] PostgreSQL")
            print("  [2] MySQL")
            choice = input(f"Select engine [1]: ").strip()
            config["engine"] = "mysql" if choice == "2" else "postgresql"
            
            config["host"] = input(f"Host [{existing.get('host', 'localhost')}]: ").strip() or existing.get("host", "localhost")
            
            default_port = default_port_for(config["engine"])
            port_input = input(f"Port [{existing.get('port', default_port)}]: ").strip()
            config["port"] = int(port_input) if port_input else existing.get("port", default_port)
            
            config["database"] = input(f"Database [{existing.get('database', '')}]: ").strip() or existing.get("database", "")
            config["user"] = input(f"Username [{existing.get('user', '')}]: ").strip() or existing.get("user", "")
            
            env_var = input(f"Password env var [{existing.get('password_env', '')}]: ").strip()
            config["password_env"] = env_var or existing.get("password_env", "")
            
            tls_input = input(f"Enable TLS? [y/N]: ").strip().lower()
            config["tls"] = tls_input == "y"
            
            config["proxy"] = "none"
            config["read_only"] = False
            config["make_default"] = input("Set as default? [y/N]: ").strip().lower() == "y"
            
            return config
            
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return None

    def configure_llm(self, cfg: TargetsConfig, kwargs: dict) -> RdstResult:
        """Configure LLM settings for RDST.

        RDST uses Anthropic's Claude for AI-powered query analysis.
        Users must provide their own API key via ANTHROPIC_API_KEY environment variable.
        """
        import os

        llm_meta = cfg.get_llm_config()
        has_existing = bool(llm_meta)

        self._show_step("", "Anthropic Configuration", "")

        # Check if API key is already set
        has_api_key = bool(os.getenv("ANTHROPIC_API_KEY"))

        if has_api_key:
            self._show_success("API Key Detected", "ANTHROPIC_API_KEY is set in your environment")

            # Use Sonnet 4.5 as the default (and only) model
            cfg._data.setdefault("llm", {})
            cfg._data["llm"]["provider"] = "claude"
            cfg._data["llm"]["model"] = "claude-sonnet-4-5-20250929"
            cfg._data["llm"]["hint"] = "Using Claude Sonnet 4.5"

            cfg.save()
            self._show_success("Configured", cfg._data["llm"]["hint"])
            return RdstResult(True, "Anthropic configured")

        else:
            # No API key set - show instructions
            self._show_warning(
                "API Key Required",
                "RDST requires an Anthropic API key for AI-powered query analysis.\n\n"
                "To get started:\n"
                "  1. Visit https://console.anthropic.com/\n"
                "  2. Create an account or sign in\n"
                "  3. Generate an API key\n"
                "  4. Set the environment variable:\n"
                "     export ANTHROPIC_API_KEY=\"sk-ant-...\"\n\n"
                "For persistence, add to ~/.bashrc or ~/.zshrc:\n"
                "  echo 'export ANTHROPIC_API_KEY=\"your-key\"' >> ~/.bashrc\n"
                "  source ~/.bashrc"
            )

            # Still save config to mark LLM as configured (will work once key is set)
            cfg._data.setdefault("llm", {})
            cfg._data["llm"]["provider"] = "claude"
            cfg._data["llm"]["model"] = "claude-sonnet-4-5-20250929"
            cfg._data["llm"]["hint"] = "Waiting for ANTHROPIC_API_KEY"
            cfg.save()

            return RdstResult(False, "ANTHROPIC_API_KEY not set. See instructions above.")