"""
Modern interactive configuration wizard for rdst CLI.
Handles database target configuration with a beautiful, step-by-step interface.
"""

from typing import List, Dict, Optional, Any
from ..llm_manager.claude_provider import AnthropicModel
from .rdst_cli import (
    RdstResult,
    TargetsConfig,
    normalize_db_type,
    default_port_for,
    ENGINES,
    PROXY_TYPES,
)

# Import UI system - handles Rich availability internally
from lib.ui import (
    StyleTokens,
    Layout as UILayout,
    TargetsTable,
    Banner,
    Confirm,
    IntPrompt,
    KeyValueTable,
    MessagePanel,
    NextSteps,
    NoticePanel,
    Prompt,
    SelectPrompt,
    SelectionTable,
    get_console,
)


class ConfigurationWizard:
    """Modern interactive configuration wizard with beautiful UI."""

    def __init__(self, console=None):
        self.console = console or get_console()

    def configure_targets(
        self, subcmd: str, cfg: TargetsConfig, **kwargs
    ) -> RdstResult:
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

    def _list_targets(
        self, cfg: TargetsConfig, kwargs: dict, show_tips: bool = False
    ) -> RdstResult:
        """Display all configured targets with modern formatting."""
        targets = cfg.list_targets()
        default_name = cfg.get_default()

        if not targets:
            msg = f"No database targets configured yet.\nRun [{StyleTokens.COMMAND}]rdst configure add[/{StyleTokens.COMMAND}] to get started."
            self._show_info("No Targets", msg)
            return RdstResult(True, "No targets configured")

        # Use TargetsTable UI component for consistent styling
        target_list = []
        for name in targets:
            target = cfg.get(name) or {}
            target_list.append(
                {
                    "name": name,
                    "engine": target.get("engine", "unknown"),
                    "host": target.get("host", "unknown"),
                    "port": target.get("port", "?"),
                    "database": target.get("database", "?"),
                    "proxy": target.get("proxy", "none"),
                    "endpoint_verified": target.get(
                        "endpoint_verified", target.get("verified", False)
                    ),
                }
            )

        table = TargetsTable(target_list, default_target=default_name)
        self.console.print(table)

        # Show helpful hints using NextSteps component
        if show_tips:
            steps = NextSteps(
                [
                    ("rdst configure default <name>", "Set default target"),
                    ("rdst configure edit <name>", "Edit target"),
                    ("rdst configure remove <name>", "Remove target"),
                    ("rdst configure list", "List targets"),
                ],
                title="Tips",
            )
            self.console.print(steps)

        return RdstResult(True, f"Found {len(targets)} configured targets")

    def _menu(self, cfg: TargetsConfig, kwargs: dict) -> RdstResult:
        """Main menu when running `rdst configure` without options.
        - If no targets exist: inform user and start the add flow.
        - If targets exist: list them and offer options to add, set default, or delete.
        """
        targets = cfg.list_targets()
        if not targets:
            # Inform and immediately start configuring the first target
            self._show_info(
                "No Targets", "No targets configured yet. Let's add your first target."
            )
            res = self._add_edit_target(cfg, "add", kwargs)
            return res

        # Show current targets first
        self._list_targets(cfg, kwargs, True)

        # Offer simple choices per requirement
        # Note: Cancel option is provided by SelectPrompt, no need for "Exit"
        choices = [
            "Add new target",
            "Edit a target",
            "Set default target",
            "Delete a target",
        ]
        choice = self._interactive_select("Choose an action", choices, default_idx=0)
        if choice is None:
            # User cancelled
            return RdstResult(True, "")
        if choice == choices[0]:
            # Add
            return self._add_edit_target(cfg, "add", kwargs)
        if choice == choices[1]:
            # Edit
            return self._add_edit_target(cfg, "edit", kwargs)
        if choice == choices[2]:
            # Set default
            name = self._interactive_select(
                "Select target to set as default", targets, default_idx=0
            )
            if not name:
                return RdstResult(False, "Selection cancelled")
            cfg.set_default(name)
            cfg.save()
            self._show_success("Default Set", f"'{name}' is now the default target")
            return RdstResult(True, f"Default target set to '{name}'")
        if choice == choices[3]:
            # Delete
            return self._remove_target(cfg, kwargs)
        # Fallback (shouldn't happen)
        return RdstResult(True, "")

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
        self._show_target_details(name, target, "Remove Target", StyleTokens.ERROR)

        # Confirm removal
        if not kwargs.get("confirm"):
            confirmed = self._confirm_action(
                f"Are you sure you want to remove target '{name}'?",
                "This action cannot be undone!",
                danger=True,
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

    def _add_edit_target(
        self, cfg: TargetsConfig, subcmd: str, kwargs: dict
    ) -> RdstResult:
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
        has_connection_string = bool(
            kwargs.get("connection_string") or kwargs.get("connection-string")
        )
        has_required_args = all(
            kwargs.get(field) for field in ["host", "user", "database"]
        )

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
            self._show_info(
                "Skipped", "Connection verification skipped (--skip-verify)"
            )
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
                    default=False,
                ):
                    return RdstResult(
                        False, "Configuration cancelled due to connection failure"
                    )

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
            f"Target '{target_name}'", f"has been {action} successfully!"
        )

        # Breadcrumb: show next steps using NextSteps component
        if not is_edit:
            steps = NextSteps(
                [
                    (f"rdst top --target {target_name}", "Monitor slow queries"),
                    (
                        f'rdst analyze -q "SELECT ..." --target {target_name}',
                        "Analyze a query",
                    ),
                    (f"rdst configure test {target_name}", "Test connection"),
                ]
            )
            self.console.print(steps)

        # Return empty message since _show_success already displayed status
        return RdstResult(
            True,
            "",
            data={
                "target": target_name,
                "config": config_data,
                "default": cfg.get_default(),
            },
        )

    def _run_configuration_wizard(
        self, existing: dict, is_edit: bool, name: str = None
    ) -> Optional[dict]:
        """Run the interactive configuration wizard."""
        # Welcome message with compact panel
        action = "Edit" if is_edit else "Create"
        welcome_text = (
            f"Let's {'update' if is_edit else 'configure'} your database connection!"
        )

        if is_edit and name:
            welcome_text += f"\n\nEditing target: [{StyleTokens.PRIMARY}]{name}[/{StyleTokens.PRIMARY}]"

        self.console.print(Banner(f"{action} Database Target"))
        self.console.print(MessagePanel(welcome_text, variant="info"))

        try:
            config = {}

            # Step 1: Basic Info
            self._show_step("Step 1", "Basic Information", "")
            config["name"] = name or self._prompt_text(
                "Target name",
                "A friendly name for this connection (e.g., prod, staging, dev)",
                existing.get("name"),
                required=True,
            )

            # Database Engine - combined with step info
            config["engine"] = self._select_database_engine_with_step(
                existing.get("engine", "postgresql")
            )

            # Step 3: Connection Details
            self._show_step("Step 3", "Connection Details", "🔌")
            config.update(self._collect_connection_details(config["engine"], existing))

            # Step 4: Security & Authentication
            self._show_step("Step 4", "Security & Authentication", "🔐")
            config.update(self._collect_security_settings(config["name"], existing))

            # Step 5: Advanced Options
            self._show_step("Step 5", "Advanced Configuration", "")
            config.update(
                self._collect_advanced_settings(existing, config.get("engine"))
            )

            # Step 6: Finalization
            self._show_step("Step 6", "Finalization", "")
            config["make_default"] = self._confirm(
                "Set as default target?",
                "Use this target when no specific target is specified",
                False,
            )

            # Show configuration summary
            self._show_config_summary(config)

            if not self._confirm(
                "Save this configuration?", "Create the database target", True
            ):
                return None

            return config

        except (EOFError, KeyboardInterrupt):
            self._show_info("Cancelled", "Configuration wizard cancelled")
            return None

    def _select_database_engine_with_step(self, current_engine: str) -> str:
        """Select database engine with integrated step information."""
        engines = ["PostgreSQL", "MySQL"]
        default_idx = 0 if current_engine == "postgresql" else 1

        # Show step header
        self.console.print()
        self.console.print(
            f"[{StyleTokens.HEADER}]Step 2: Database Engine[/{StyleTokens.HEADER}]"
        )

        # Use SelectPrompt for selection
        engine_choice = SelectPrompt.ask(
            "Choose database engine",
            options=engines,
            default=default_idx + 1,
            return_index=False,
        )

        return engine_choice.lower() if engine_choice else "postgresql"

    def _select_database_engine(self, current_engine: str) -> str:
        """Select database engine with simplified interface."""
        engines = ["PostgreSQL", "MySQL"]
        default_idx = 0 if current_engine == "postgresql" else 1

        # Use SelectPrompt for selection
        engine_choice = SelectPrompt.ask(
            "Select database engine",
            options=engines,
            default=default_idx + 1,
            return_index=False,
        )

        return engine_choice.lower() if engine_choice else "postgresql"

    def _collect_connection_details(self, engine: str, existing: dict) -> dict:
        """Collect database connection details."""
        config = {}

        config["host"] = self._prompt_text(
            "Database host",
            "Hostname or IP address of your database server",
            existing.get("host") or "localhost",
            required=True,
        )

        default_port = default_port_for(engine)
        port_input = self._prompt_text(
            "Port",
            f"Database port number (default: {default_port})",
            str(existing.get("port", default_port)),
        )
        config["port"] = int(port_input) if port_input else default_port

        config["database"] = self._prompt_text(
            "Database name",
            "Name of the database to connect to",
            existing.get("database"),
            required=True,
        )

        config["user"] = self._prompt_text(
            "Username",
            "Database username for authentication",
            existing.get("user"),
            required=True,
        )

        return config

    def _collect_security_settings(self, target_name: str, existing: dict) -> dict:
        """Collect security and authentication settings."""
        config = {}

        # Password handling (simplified)
        password_options = [
            "Environment variable (recommended)",
            "Skip password configuration",
        ]

        password_previews = None

        password_choice = self._interactive_select_with_preview(
            "How should we handle the password?", password_options, 0, password_previews
        )

        if "Environment variable" in password_choice:
            suggested_var = f"{target_name.upper().replace('-', '_')}_PASSWORD"
            config["password_env"] = self._prompt_text(
                "Environment variable name",
                "Name of the environment variable containing the password",
                existing.get("password_env") or suggested_var,
            )
        else:
            config["password_env"] = ""

        # TLS Configuration
        config["tls"] = self._confirm(
            "Enable TLS/SSL encryption?",
            "Encrypts communication with the database",
            existing.get("tls", True),
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

    def _collect_advanced_settings(
        self, existing: dict, engine: Optional[str] = None
    ) -> dict:
        """Collect advanced configuration settings.
        Shows only relevant proxy types based on the SQL engine.
        """
        config = {}

        # Build engine-aware proxy list
        allowed_values = self._allowed_proxies_for_engine(
            engine or existing.get("engine")
        )
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
        default_proxy_idx = (
            proxy_values.index(current_proxy) if current_proxy in proxy_values else 0
        )

        proxy_choice = self._interactive_select_with_preview(
            "Select proxy type", proxy_options, default_proxy_idx, proxy_previews
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
            existing.get("read_only", False),
        )

        return config

    # UI Helper Methods
    def _show_step(self, step: str, title: str, emoji: str):
        """Show a wizard step header."""
        self.console.print()
        if step:
            self.console.print(
                f"[{StyleTokens.HEADER}]{step}: {title}[/{StyleTokens.HEADER}]"
            )
        else:
            self.console.print(f"[{StyleTokens.HEADER}]{title}[/{StyleTokens.HEADER}]")

    def _prompt_text(
        self, label: str, description: str, default: str = None, required: bool = False
    ) -> str:
        """Modern text prompt with rich formatting."""
        # Show description
        if description:
            self.console.print(
                f"[{StyleTokens.MUTED}]{description}[/{StyleTokens.MUTED}]"
            )

        return Prompt.ask(
            f"[{StyleTokens.HEADER}]{label}[/{StyleTokens.HEADER}]",
            default=default or "",
            show_default=bool(default),
        )

    def _prompt(self, label: str, default: str = "") -> str:
        """Simple text prompt for user input."""
        return Prompt.ask(
            f"[{StyleTokens.HEADER}]{label}[/{StyleTokens.HEADER}]",
            default=default or "",
            show_default=bool(default),
        )

    def _confirm(
        self, question: str, description: str = None, default: bool = True
    ) -> bool:
        """Modern confirmation prompt."""
        if description:
            self.console.print(
                f"[{StyleTokens.MUTED}]{description}[/{StyleTokens.MUTED}]"
            )

        return Confirm.ask(
            f"[{StyleTokens.HEADER}]{question}[/{StyleTokens.HEADER}]", default=default
        )

    def _interactive_select(
        self, prompt: str, choices: List[str], default_idx: int = 0
    ) -> Optional[str]:
        """Interactive selection with modern UI."""
        try:
            return SelectPrompt.ask(
                prompt,
                options=choices,
                default=default_idx + 1,
                return_index=False,
                allow_cancel=False,
            )
        except (EOFError, KeyboardInterrupt):
            return None

    def _interactive_select_with_preview(
        self,
        prompt: str,
        choices: List[str],
        default_idx: int = 0,
        previews: List[str] = None,
    ) -> Optional[str]:
        """Interactive selection with preview descriptions."""
        # Always use simplified selection without previews to keep UI consistent
        return self._interactive_select(prompt, choices, default_idx)

    def _confirm_action(
        self, question: str, warning: str = None, danger: bool = False
    ) -> bool:
        """Confirm a potentially destructive action."""
        if warning:
            self.console.print(
                f"[{StyleTokens.WARNING}]{warning}[/{StyleTokens.WARNING}]"
            )

        return Confirm.ask(question, default=False)

    def _show_target_details(self, name: str, target: dict, title: str, style: str):
        """Show target details in a panel."""
        verified_flag = bool(
            target.get("endpoint_verified", target.get("verified", False))
        )
        data = {
            "Engine": target.get("engine", "unknown"),
            "Host": target.get("host", "unknown"),
            "Database": target.get("database", "unknown"),
            "Verified": "Yes" if verified_flag else "No",
        }

        self.console.print(f"[{style}]{title}: {name}[/{style}]")
        table = KeyValueTable(data)
        self.console.print(table)

    def _show_config_summary(self, config: dict):
        """Show a beautiful configuration summary."""
        # Format values nicely
        display_data = {
            "Name": config.get("name", ""),
            "Engine": {"postgresql": "PostgreSQL", "mysql": "MySQL"}.get(
                config.get("engine", ""), config.get("engine", "")
            ),
            "Host": config.get("host", ""),
            "Port": str(config.get("port", "")),
            "Database": config.get("database", ""),
            "User": config.get("user", ""),
            "Password Env": config.get("password_env", "") or "Not configured",
            "TLS": "Enabled" if config.get("tls") else "Disabled",
            "Proxy": config.get("proxy", "none").title(),
            "Read Only": "Yes" if config.get("read_only") else "No",
        }

        # Use KeyValueTable for consistent styling
        self.console.print()
        table = KeyValueTable(display_data, title="Configuration Summary")
        self.console.print(table)

    def _show_success(self, title: str, message: str):
        self.console.print(MessagePanel(message, variant="success", title=title))

    def _show_info(self, title: str, message: str):
        self.console.print(MessagePanel(message, variant="info", title=title))

    def _show_warning(self, title: str, message: str):
        self.console.print(MessagePanel(message, variant="warning", title=title))

    def _show_error(self, title: str, message: str):
        self.console.print(MessagePanel(message, variant="error", title=title))

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
                    f'Set it with: export {password_env}="your_password"',
                )

        self._show_info(
            "Testing Connection", f"Connecting to {host}:{port}/{database}..."
        )

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
                    sslmode="require" if tls else "prefer",
                )
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                conn.close()
                return RdstResult(
                    True, f"Connected successfully!\nServer: {version[:80]}..."
                )

            elif engine == "mysql":
                import pymysql

                conn = pymysql.connect(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database,
                    connect_timeout=10,
                    ssl={"ssl": {}} if tls else None,
                )
                cursor = conn.cursor()
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]
                cursor.close()
                conn.close()
                return RdstResult(
                    True, f"Connected successfully!\nServer: MySQL {version}"
                )

            else:
                return RdstResult(False, f"Unknown engine: {engine}")

        except ImportError as e:
            driver = "psycopg2" if engine == "postgresql" else "pymysql"
            return RdstResult(
                False,
                f"Missing database driver: {driver}\n"
                f"Install with: pip install {driver}",
            )
        except Exception as e:
            error_msg = str(e)
            # Clean up common error messages
            if (
                "could not connect" in error_msg.lower()
                or "connection refused" in error_msg.lower()
            ):
                return RdstResult(
                    False,
                    f"Connection refused: Cannot reach {host}:{port}\n"
                    f"Check that the database is running and accessible.",
                )
            elif (
                "authentication failed" in error_msg.lower()
                or "access denied" in error_msg.lower()
            ):
                return RdstResult(
                    False,
                    f"Authentication failed for user '{user}'\n"
                    f"Check your username and password.",
                )
            elif "does not exist" in error_msg.lower():
                return RdstResult(
                    False,
                    f"Database '{database}' does not exist.\nCheck the database name.",
                )
            elif "timeout" in error_msg.lower():
                return RdstResult(
                    False,
                    f"Connection timed out to {host}:{port}\n"
                    f"Check network connectivity and firewall rules.",
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
            "database": "Database name",
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
            errors.append(
                f"For engine '{engine}', proxy must be one of: {', '.join(allowed_proxies)}"
            )

        if errors:
            # Create compact error display
            error_list = "\n".join([f"• {error}" for error in errors])
            error_content = f"Configuration validation failed:\n\n{error_list}"

            self._show_error("Validation Error", error_content)
            return RdstResult(False, error_content)

        return RdstResult(True, "Configuration is valid")

    def _collect_config_from_args(
        self, kwargs: dict, existing: dict, name: str = None
    ) -> dict:
        """Collect configuration from command line arguments.

        If --connection-string is provided, parse it first and use individual flags to override.
        """
        from .rdst_cli import parse_connection_string

        parsed_values = {}

        # Parse connection string if provided
        connection_string = kwargs.get("connection_string") or kwargs.get(
            "connection-string"
        )
        password_from_connstring = None
        if connection_string:
            try:
                parsed_values = parse_connection_string(connection_string)
                # If password was in connection string, save it for env var prompt
                if parsed_values.get("password"):
                    password_from_connstring = parsed_values.get("password")
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
                kwargs.get("engine")
                or parsed_values.get("engine")
                or existing.get("engine")
            )
            or "postgresql",
            "host": (
                kwargs.get("host") or parsed_values.get("host") or existing.get("host")
            ),
            "port": (
                kwargs.get("port")
                or parsed_values.get("port")
                or existing.get("port")
                or default_port_for(
                    kwargs.get("engine") or parsed_values.get("engine", "postgresql")
                )
            ),
            "user": (
                kwargs.get("user") or parsed_values.get("user") or existing.get("user")
            ),
            "database": (
                kwargs.get("database")
                or parsed_values.get("database")
                or existing.get("database")
            ),
            "password_env": self._resolve_password_env(
                kwargs, existing, target_name, password_from_connstring
            ),
            "read_only": bool(
                kwargs.get("read_only")
                or kwargs.get("read-only")
                or existing.get("read_only", False)
            ),
            "proxy": (kwargs.get("proxy") or existing.get("proxy") or "none").lower(),
            "tls": self._resolve_tls_flag(kwargs, existing, parsed_values),
            "make_default": bool(kwargs.get("default")),
        }

        return config

    def _resolve_password_env(
        self,
        kwargs: dict,
        existing: dict,
        target_name: str,
        password_from_connstring: str = None,
    ) -> str:
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
            suggested_var = (
                f"{target_name.upper().replace('-', '_')}_PASSWORD"
                if target_name
                else "DB_PASSWORD"
            )
            self._show_warning(
                "Password in Connection String",
                f"Found password in connection string.\n"
                f"For security, rdst stores passwords in environment variables.\n\n"
                f'Suggested: export {suggested_var}="{password_from_connstring}"\n\n'
                f"You can override with --password-env flag.",
            )
            return suggested_var

        return ""

    def _resolve_tls_flag(
        self, kwargs: dict, existing: dict, parsed_values: dict = None
    ) -> bool:
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

    def configure_llm(self, cfg: TargetsConfig, kwargs: dict) -> RdstResult:
        """Configure LLM settings for RDST.

        RDST uses Anthropic's Claude for AI-powered query analysis.
        Users can provide their own API key or sign up for a free trial.
        """
        import os

        llm_meta = cfg.get_llm_config()
        has_existing = bool(llm_meta)

        self._show_step("", "Anthropic Configuration", "")

        # Check if API key is already set (own key takes priority)
        has_api_key = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("RDST_TRIAL_TOKEN"))

        if has_api_key:
            self._show_success(
                "API Key Detected", "Anthropic API key is set in your environment"
            )

            cfg._data.setdefault("llm", {})
            cfg._data["llm"]["provider"] = "claude"
            cfg._data["llm"]["model"] = AnthropicModel.SONNET_4_5.value
            cfg._data["llm"]["hint"] = "Using Claude Sonnet 4.5"

            cfg.save()
            self._show_success("Configured", cfg._data["llm"]["hint"])
            return RdstResult(True, "Anthropic configured")

        else:
            # No API key - offer trial or manual setup
            self.console.print(
                MessagePanel(
                    "RDST requires an Anthropic API key for AI-powered query analysis.",
                    variant="info",
                    title="LLM Setup",
                )
            )

            # Check for existing active trial
            if cfg.is_trial_active():
                trial = cfg.get_trial_config()
                remaining = trial.get("remaining_cents")
                limit = trial.get("limit_cents", 500)
                if remaining is not None:
                    from ..llm_manager.trial_display import cents_to_tokens, format_tokens
                    remaining_tok = cents_to_tokens(remaining)
                    limit_tok = cents_to_tokens(limit)
                    used_tok = limit_tok - remaining_tok
                    pct = int((remaining / limit) * 100) if limit > 0 else 0
                    balance_msg = (
                        f"You have an active RDST trial\n\n"
                        f"  Balance: {format_tokens(remaining_tok)} of {format_tokens(limit_tok)} tokens remaining ({pct}%)\n"
                        f"  Used:    {format_tokens(used_tok)} tokens"
                    )
                else:
                    balance_msg = "You have an active RDST trial (balance updates after next LLM call)"
                self._show_success("Active Trial", balance_msg)
                cfg._data.setdefault("llm", {})
                cfg._data["llm"]["provider"] = "claude"
                cfg._data["llm"]["model"] = AnthropicModel.SONNET_4_5.value
                cfg._data["llm"]["hint"] = "Using trial credits"
                cfg.save()
                return RdstResult(True, "Trial active")

            choice = SelectPrompt.ask(
                "How would you like to set up AI query analysis?",
                options=[
                    "Get free trial credits (no credit card needed)",
                    "I have my own Anthropic API key",
                    "Skip for now",
                ],
                default=1,
                return_index=True,
            )

            trial_success = False
            if choice == 1:  # Free trial
                trial_success = self._run_trial_registration(cfg)
            elif choice == 2:  # Own key
                self.console.print(
                    MessagePanel(
                        "Set the environment variable:\n"
                        '  export ANTHROPIC_API_KEY="sk-ant-..."\n\n'
                        "Get a key at: https://console.anthropic.com/\n\n"
                        "For persistence, add to ~/.bashrc or ~/.zshrc:\n"
                        '  echo \'export ANTHROPIC_API_KEY="your-key"\' >> ~/.bashrc\n'
                        "  source ~/.bashrc",
                        variant="info",
                        title="API Key Setup",
                    )
                )
            # choice == 3: Skip

            # Save config regardless
            cfg._data.setdefault("llm", {})
            cfg._data["llm"]["provider"] = "claude"
            cfg._data["llm"]["model"] = AnthropicModel.SONNET_4_5.value
            cfg._data["llm"]["hint"] = "Using trial credits" if trial_success else "Waiting for API key"
            cfg.save()

            return RdstResult(trial_success, "LLM configured" if trial_success else "API key needed")

    def _run_trial_registration(self, cfg: TargetsConfig) -> bool:
        """Run the trial registration flow with email validation retry loop.

        1. Prompt for email (with retry on validation errors)
        2. POST /register to key service
        3. Handle: disposable, invalid domain, bad email, send failure
        4. Tell user to check email
        5. Prompt for trial token (from verification page)
        6. Save to config
        """
        import requests as req

        REGISTER_URL = "https://rdst-keyservice.readysetio.workers.dev/register"

        # Retry loop for email validation errors
        max_attempts = 3
        for attempt in range(max_attempts):
            email = Prompt.ask("Email address")
            if not email or "@" not in email:
                self.console.print(MessagePanel("Invalid email address", variant="error"))
                if attempt < max_attempts - 1:
                    continue
                return False

            # Call registration endpoint
            try:
                resp = req.post(REGISTER_URL, json={"email": email}, timeout=20)
            except Exception:
                self.console.print(
                    MessagePanel(
                        "Unable to reach RDST trial service.\n\n"
                        "You can set your own key instead:\n"
                        '  export ANTHROPIC_API_KEY="sk-ant-..."',
                        variant="error",
                        title="Connection Error",
                    )
                )
                return False

            # Parse response JSON once
            try:
                resp_data = resp.json()
            except Exception:
                resp_data = {}

            code = resp_data.get("code", "")

            # --- Non-retryable errors ---

            if resp.status_code == 503:
                detail = resp_data.get("detail", "")
                self.console.print(
                    MessagePanel(
                        detail or "The RDST free trial program is currently full.\n\n"
                        "Options:\n"
                        "  1. Email hello@readyset.io to request access\n"
                        '  2. Use your own key: export ANTHROPIC_API_KEY="sk-ant-..."\n'
                        "     Get one at: https://console.anthropic.com/",
                        variant="warning",
                        title="Trial Program Full",
                    )
                )
                return False

            if resp.status_code == 429:
                self.console.print(
                    MessagePanel(
                        "Too many registration attempts. Please try again later.",
                        variant="warning",
                        title="Rate Limited",
                    )
                )
                return False

            if resp.status_code == 409:
                self.console.print(
                    MessagePanel("This email is already registered.", variant="warning")
                )
                # Still let them enter a token if they lost it
                break

            # --- Retryable email validation errors ---

            if resp.status_code == 400 and code == "DISPOSABLE_EMAIL":
                self.console.print(
                    MessagePanel(
                        "Disposable or temporary email addresses are not allowed.\n\n"
                        "Please use your real email address (work or personal).",
                        variant="error",
                        title="Invalid Email",
                    )
                )
                if attempt < max_attempts - 1:
                    self.console.print("  Try again with a different email.\n")
                    continue
                return False

            if resp.status_code == 400 and code == "INVALID_DOMAIN":
                self.console.print(
                    MessagePanel(
                        "This email domain doesn't appear to accept mail.\n\n"
                        "Please check for typos and try again.",
                        variant="error",
                        title="Invalid Email Domain",
                    )
                )
                if attempt < max_attempts - 1:
                    continue
                return False

            if resp.status_code == 400 and code == "EMAIL_REJECTED":
                did_you_mean = resp_data.get("did_you_mean")
                detail = resp_data.get("detail", "This email could not be verified.")
                if did_you_mean:
                    self.console.print(
                        MessagePanel(
                            f"{detail}\n\n"
                            f"Suggestion: {did_you_mean}",
                            variant="warning",
                            title="Email Validation Failed",
                        )
                    )
                else:
                    self.console.print(
                        MessagePanel(detail, variant="error", title="Email Validation Failed")
                    )
                if attempt < max_attempts - 1:
                    continue
                return False

            # --- Email send failure (422) ---

            if resp.status_code == 422:
                email_error = resp_data.get("email_error", "")
                hint = resp_data.get("hint", "")
                self.console.print(
                    MessagePanel(
                        f"Could not send verification email to {email}.\n\n"
                        f"{email_error}\n\n"
                        f"{hint}" if hint else
                        f"Could not send verification email to {email}.\n\n"
                        f"{email_error}\n\n"
                        "This usually means the email address doesn't exist or can't receive mail.\n"
                        "Please double-check and try again.",
                        variant="error",
                        title="Email Delivery Failed",
                    )
                )
                if attempt < max_attempts - 1:
                    continue
                return False

            # --- Other errors ---

            if resp.status_code >= 400:
                detail = resp_data.get("detail", f"Registration failed (HTTP {resp.status_code})")
                self.console.print(MessagePanel(detail, variant="error"))
                return False

            # --- Success ---

            limit_display = resp_data.get("limit_display", "$5.00")
            email_tier = resp_data.get("email_tier", "business")

            self.console.print(
                MessagePanel(
                    f"Verification email sent to {email}\n\n"
                    f"Your trial credit: {limit_display}"
                    f"{' (business email)' if email_tier == 'business' else ' (personal email)'}\n\n"
                    "1. Check your email (including spam folder)\n"
                    "2. Click the verification link\n"
                    "3. Copy the trial token from the page",
                    variant="success",
                    title="Email Sent",
                )
            )
            break
        else:
            # Exhausted all retry attempts
            self.console.print(
                MessagePanel(
                    "Too many failed attempts.\n\n"
                    "You can set your own key instead:\n"
                    '  export ANTHROPIC_API_KEY="sk-ant-..."',
                    variant="error",
                )
            )
            return False

        # Prompt for trial token
        token = Prompt.ask("Paste your trial token")
        if not token or len(token.strip()) < 10:
            self.console.print(MessagePanel("Invalid token", variant="error"))
            return False

        # Save trial config
        cfg.set_trial_config({
            "token": token.strip(),
            "email": email,
            "status": "active",
        })
        cfg.save()

        self.console.print(
            MessagePanel(
                "Trial activated! Check your verification email for credit details.",
                variant="success",
                title="Trial Active",
            )
        )
        return True
