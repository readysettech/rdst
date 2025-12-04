"""
Telemetry Manager for RDST

Handles:
- Device ID generation and persistence
- PostHog event tracking
- Sentry crash reporting
- Slack webhook notifications
- Usage statistics tracking
- Privacy controls (opt-out)
"""

import os
import sys
import uuid
import platform
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import json

# Will be lazy-imported to avoid startup cost
_posthog = None
_sentry_sdk = None
_requests = None


def _get_posthog():
    global _posthog
    if _posthog is None:
        try:
            import posthog
            _posthog = posthog
        except ImportError:
            _posthog = False
    return _posthog if _posthog else None


def _get_sentry():
    global _sentry_sdk
    if _sentry_sdk is None:
        try:
            import sentry_sdk
            _sentry_sdk = sentry_sdk
        except ImportError:
            _sentry_sdk = False
    return _sentry_sdk if _sentry_sdk else None


def _get_requests():
    global _requests
    if _requests is None:
        try:
            import requests
            _requests = requests
        except ImportError:
            _requests = False
    return _requests if _requests else None


class TelemetryManager:
    """
    Manages all telemetry for RDST.

    Features:
    - Pseudonymous device ID (stored in ~/.rdst/device_id)
    - PostHog for usage analytics
    - Sentry for crash reporting
    - Slack webhooks for alerts
    - Cumulative usage stats per device
    - Privacy controls (opt-out via env var or config)
    """

    # Configuration
    POSTHOG_API_KEY = os.environ.get("RDST_POSTHOG_KEY", "phc_WPINnbS1CUiADz01QFeDZCr4Wn7jXfNPxe1EK0V2ZzP")
    POSTHOG_HOST = "https://us.i.posthog.com"
    SENTRY_DSN = os.environ.get("RDST_SENTRY_DSN", "")  # Optional - not currently used
    # Slack webhooks - only used for: installation, first successful analyze, user feedback
    SLACK_WEBHOOK_INSTALLS = os.environ.get("RDST_SLACK_WEBHOOK_INSTALLS", "https://hooks.slack.com/services/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")
    SLACK_WEBHOOK_FEEDBACK = os.environ.get("RDST_SLACK_WEBHOOK_FEEDBACK", "https://hooks.slack.com/services/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")
    SLACK_WEBHOOK_ANALYZE = os.environ.get("RDST_SLACK_WEBHOOK_ANALYZE", "https://hooks.slack.com/services/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")

    def __init__(self):
        self._device_id: Optional[str] = None
        self._enabled: Optional[bool] = None
        self._initialized = False
        self._stats: Optional[Dict[str, int]] = None
        self._rdst_dir = Path.home() / ".rdst"
        self._lock = threading.Lock()

    def _ensure_initialized(self):
        """Lazy initialization to avoid startup cost."""
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            # Initialize PostHog
            posthog = _get_posthog()
            if posthog and self.POSTHOG_API_KEY and self.is_enabled():
                try:
                    posthog.api_key = self.POSTHOG_API_KEY
                    posthog.host = self.POSTHOG_HOST
                except Exception:
                    pass

            # Initialize Sentry
            sentry = _get_sentry()
            if sentry and self.SENTRY_DSN and self.is_enabled():
                try:
                    sentry.init(
                        dsn=self.SENTRY_DSN,
                        traces_sample_rate=0.1,
                        environment=os.environ.get("RDST_ENV", "production"),
                        release=self._get_version(),
                    )
                    # Set user context with device_id
                    sentry.set_user({"id": self.device_id})
                except Exception:
                    pass

            self._initialized = True

    @property
    def device_id(self) -> str:
        """Get or create a persistent device ID."""
        if self._device_id:
            return self._device_id

        device_id_file = self._rdst_dir / "device_id"
        is_new_install = False

        # Try to read existing
        if device_id_file.exists():
            try:
                self._device_id = device_id_file.read_text().strip()
                if self._device_id:
                    return self._device_id
            except Exception:
                pass

        # Generate new - this is a new installation
        self._device_id = str(uuid.uuid4())
        is_new_install = True

        # Persist
        try:
            self._rdst_dir.mkdir(parents=True, exist_ok=True)
            device_id_file.write_text(self._device_id)
        except Exception:
            pass

        # Track new installation (deferred to avoid recursion)
        if is_new_install:
            self._schedule_installation_tracking()

        return self._device_id

    def _schedule_installation_tracking(self):
        """Schedule installation tracking in a background thread to avoid recursion."""
        def track():
            try:
                # Determine install method
                install_method = "unknown"
                import shutil
                if shutil.which("pipx") and "pipx" in sys.prefix:
                    install_method = "pipx"
                elif shutil.which("uvx") and "uv" in sys.prefix:
                    install_method = "uvx"
                elif "site-packages" in __file__:
                    install_method = "pip"
                else:
                    install_method = "source"

                self.track_installation(install_method)
            except Exception:
                pass

        # Run in background to not block
        thread = threading.Thread(target=track, daemon=True)
        thread.start()

    def is_enabled(self) -> bool:
        """Check if telemetry is enabled."""
        if self._enabled is not None:
            return self._enabled

        # Disable telemetry during tests
        if os.environ.get("RDST_TESTING", "").lower() in ("true", "1", "yes"):
            self._enabled = False
            return False

        # Check environment variable
        env_val = os.environ.get("RDST_TELEMETRY", "").lower()
        if env_val in ("false", "0", "no", "off", "disable", "disabled"):
            self._enabled = False
            return False

        # Check config file
        config_file = self._rdst_dir / "config.toml"
        if config_file.exists():
            try:
                content = config_file.read_text()
                if "telemetry_enabled = false" in content.lower():
                    self._enabled = False
                    return False
            except Exception:
                pass

        self._enabled = True
        return True

    def _get_version(self) -> str:
        """Get RDST version."""
        # Hardcoded for now until proper versioning is set up.
        # Once published to PyPI, this should use importlib.metadata.version("rdst")
        return "0.1.0"

    def _get_base_properties(self) -> Dict[str, Any]:
        """Get base properties included with every event."""
        return {
            "device_id": self.device_id,
            "rdst_version": self._get_version(),
            "os": platform.system(),
            "os_version": platform.release(),
            "python_version": platform.python_version(),
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _get_stats(self) -> Dict[str, int]:
        """Get cumulative usage stats for this device."""
        if self._stats is not None:
            return self._stats

        stats_file = self._rdst_dir / "stats.json"

        if stats_file.exists():
            try:
                self._stats = json.loads(stats_file.read_text())
                return self._stats
            except Exception:
                pass

        # Default stats
        self._stats = {
            "total_analyzes": 0,
            "total_interactive": 0,
            "total_top_runs": 0,
            "total_cache_runs": 0,
            "total_queries_saved": 0,
            "first_seen": datetime.utcnow().isoformat(),
            "targets_configured": 0,
            # Token usage tracking
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "token_usage_by_model": {},  # {"claude-sonnet-4-20250514": {"input": X, "output": Y}}
        }
        return self._stats

    def _save_stats(self):
        """Persist stats to disk."""
        if self._stats is None:
            return
        try:
            stats_file = self._rdst_dir / "stats.json"
            self._rdst_dir.mkdir(parents=True, exist_ok=True)
            stats_file.write_text(json.dumps(self._stats, indent=2))
        except Exception:
            pass

    def _increment_stat(self, key: str, amount: int = 1):
        """Increment a stat counter."""
        stats = self._get_stats()
        stats[key] = stats.get(key, 0) + amount
        self._save_stats()

    def track(self, event: str, properties: Optional[Dict[str, Any]] = None):
        """
        Track an event to PostHog.

        Args:
            event: Event name (e.g., "analyze_run", "installation")
            properties: Additional properties to include
        """
        if not self.is_enabled():
            return

        self._ensure_initialized()

        posthog = _get_posthog()
        if not posthog or not self.POSTHOG_API_KEY:
            return

        try:
            all_props = self._get_base_properties()
            if properties:
                all_props.update(properties)

            # Fire and forget in background thread
            def send():
                try:
                    posthog.capture(
                        distinct_id=self.device_id,
                        event=event,
                        properties=all_props
                    )
                except Exception:
                    pass

            thread = threading.Thread(target=send, daemon=True)
            thread.start()

        except Exception:
            pass

    def track_with_stats(self, event: str, properties: Optional[Dict[str, Any]] = None):
        """Track an event and include cumulative device stats."""
        stats = self._get_stats()

        all_props = properties.copy() if properties else {}
        all_props["device_stats"] = stats

        self.track(event, all_props)

    # =========================================================================
    # NPS Prompt (periodic feedback)
    # =========================================================================

    def should_show_nps_prompt(self) -> bool:
        """Check if we should show the NPS prompt (every ~100 commands)."""
        stats = self._get_stats()
        total_commands = (
            stats.get("total_analyzes", 0) +
            stats.get("total_top_runs", 0) +
            stats.get("total_cache_runs", 0)
        )
        last_nps_at = stats.get("last_nps_prompt_at", 0)

        # Show every 100 commands, but not more than once per session
        if total_commands > 0 and total_commands >= last_nps_at + 100:
            return True
        return False

    def show_nps_prompt(self) -> bool:
        """
        Show the NPS prompt and handle response.
        Returns True if user responded, False if skipped.
        """
        import sys

        if not sys.stdin.isatty():
            return False

        try:
            print("\n┌─────────────────────────────────────────────────┐")
            print("│  Quick question: How's RDST working for you?   │")
            print("│  [1] 👍 Great    [2] 👎 Not great    [Enter] Skip│")
            print("└─────────────────────────────────────────────────┘")

            response = input("> ").strip()

            # Update last prompt time
            stats = self._get_stats()
            total_commands = (
                stats.get("total_analyzes", 0) +
                stats.get("total_top_runs", 0) +
                stats.get("total_cache_runs", 0)
            )
            stats["last_nps_prompt_at"] = total_commands
            self._save_stats()

            if response == "1":
                # Positive - just track it
                self.track("nps_response", {"rating": "positive", "score": 1})
                print("Thanks! 🙏")
                return True

            elif response == "2":
                # Negative - ask for more details
                print("\nWhat can we improve? (press Enter twice to submit)")
                lines = []
                while True:
                    line = input("> ").strip()
                    if not line and lines:
                        break
                    elif line:
                        lines.append(line)

                feedback = "\n".join(lines) if lines else "No details provided"

                # Track to PostHog (NPS feedback goes to PostHog only, not Slack)
                self.track("nps_response", {"rating": "negative", "score": 0, "feedback": feedback})
                print("Thanks for the feedback! We'll work on it.")
                return True

            else:
                # Skipped
                self.track("nps_response", {"rating": "skipped"})
                return False

        except (EOFError, KeyboardInterrupt):
            return False

    # =========================================================================
    # Specific Event Trackers
    # =========================================================================

    def track_installation(self, install_method: str = "unknown"):
        """Track a new installation."""
        self._increment_stat("installations", 1)

        properties = {
            "install_method": install_method,  # pipx, uvx, pip, source
            "shell": os.environ.get("SHELL", "unknown"),
            "terminal": os.environ.get("TERM", "unknown"),
        }

        self.track_with_stats("installation", properties)
        self._slack_notify_install(properties)

    def track_analyze(
        self,
        query_hash: str,
        mode: str = "standard",  # standard, fast, interactive, readyset
        duration_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        success: bool = True,
        error_type: Optional[str] = None,
        target_engine: str = "unknown",
    ):
        """Track an analyze command.

        Note: Query text is intentionally NOT sent to telemetry for privacy.
        Users can explicitly share queries via 'rdst report' if they choose.
        """
        # Check if this is the first successful analyze before incrementing
        stats = self._get_stats()
        is_first_success = success and stats.get("successful_analyzes", 0) == 0

        self._increment_stat("total_analyzes", 1)
        if success:
            self._increment_stat("successful_analyzes", 1)
        if mode == "interactive":
            self._increment_stat("total_interactive", 1)

        properties = {
            "query_hash": query_hash,
            "mode": mode,
            "duration_ms": duration_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "success": success,
            "target_engine": target_engine,
        }
        if error_type:
            properties["error_type"] = error_type

        self.track_with_stats("analyze_run", properties)

        # Slack notification only for first successful analyze
        # (failed analyze goes to PostHog only)
        if is_first_success:
            self._slack_notify_first_analyze(target_engine, duration_ms)

    def track_top(
        self,
        mode: str = "snapshot",  # snapshot, interactive
        duration_seconds: int = 0,
        queries_found: int = 0,
        target_engine: str = "unknown",
    ):
        """Track a top command."""
        self._increment_stat("total_top_runs", 1)

        properties = {
            "mode": mode,
            "duration_seconds": duration_seconds,
            "queries_found": queries_found,
            "target_engine": target_engine,
        }

        self.track_with_stats("top_run", properties)

    def track_cache(
        self,
        query_hash: str,
        result: str,  # cached, not_supported, error
        target_engine: str = "unknown",
    ):
        """Track a cache command."""
        self._increment_stat("total_cache_runs", 1)

        properties = {
            "query_hash": query_hash,
            "result": result,
            "target_engine": target_engine,
        }

        self.track_with_stats("cache_run", properties)

    def track_query_command(self, subcommand: str, query_hash: Optional[str] = None):
        """Track a query subcommand (add, list, delete, etc.)."""
        if subcommand == "add":
            self._increment_stat("total_queries_saved", 1)

        properties = {
            "subcommand": subcommand,
        }
        if query_hash:
            properties["query_hash"] = query_hash

        self.track("query_command", properties)

    def track_configure(self, action: str, engine: Optional[str] = None):
        """Track configuration actions."""
        if action == "target_add":
            self._increment_stat("targets_configured", 1)

        properties = {
            "action": action,
        }
        if engine:
            properties["engine"] = engine

        self.track("configure", properties)

    def track_llm_usage(
        self,
        provider: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        duration_ms: int,
        purpose: str,  # analyze, rewrite, index_suggestion, etc.
    ):
        """Track LLM API usage and persist cumulative token counts locally."""
        # Always persist token usage locally (even if telemetry is disabled)
        self._persist_token_usage(model, tokens_in, tokens_out)

        # Send to PostHog (if enabled)
        properties = {
            "provider": provider,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "total_tokens": tokens_in + tokens_out,
            "duration_ms": duration_ms,
            "purpose": purpose,
        }

        self.track("llm_usage", properties)

    def _persist_token_usage(self, model: str, tokens_in: int, tokens_out: int):
        """Persist cumulative token usage to local stats.json."""
        stats = self._get_stats()

        # Update totals
        stats["total_input_tokens"] = stats.get("total_input_tokens", 0) + tokens_in
        stats["total_output_tokens"] = stats.get("total_output_tokens", 0) + tokens_out
        stats["total_tokens"] = stats.get("total_tokens", 0) + tokens_in + tokens_out

        # Update per-model tracking
        model_stats = stats.get("token_usage_by_model", {})
        if model not in model_stats:
            model_stats[model] = {"input": 0, "output": 0}
        model_stats[model]["input"] = model_stats[model].get("input", 0) + tokens_in
        model_stats[model]["output"] = model_stats[model].get("output", 0) + tokens_out
        stats["token_usage_by_model"] = model_stats

        self._save_stats()

    # =========================================================================
    # Crash Reporting (Sentry)
    # =========================================================================

    def report_crash(
        self,
        exception: Exception,
        context: Optional[Dict[str, Any]] = None,
    ):
        """
        Report a crash to Sentry.

        Args:
            exception: The exception that occurred
            context: Additional context (command, query_hash, etc.)

        Note: Query text is intentionally NOT sent for privacy.
        Users can explicitly share queries via 'rdst report' if they choose.
        """
        if not self.is_enabled():
            return

        self._ensure_initialized()

        sentry = _get_sentry()
        if not sentry or not self.SENTRY_DSN:
            return

        try:
            # Add context
            with sentry.push_scope() as scope:
                scope.set_user({"id": self.device_id})

                if context:
                    for key, value in context.items():
                        scope.set_tag(key, str(value))

                scope.set_extra("device_stats", self._get_stats())

                sentry.capture_exception(exception)

            # Note: Crash notifications go to Sentry only, not Slack

        except Exception:
            pass

    # =========================================================================
    # User Feedback (rdst report)
    # =========================================================================

    def submit_feedback(
        self,
        reason: str,
        query_hash: Optional[str] = None,
        query_sql: Optional[str] = None,
        plan_json: Optional[str] = None,
        suggestion_text: Optional[str] = None,
        sentiment: str = "neutral",  # positive, negative, neutral
        email: Optional[str] = None,
        include_query: bool = False,
        include_plan: bool = False,
        flags_used: Optional[list] = None,
    ):
        """
        Submit user feedback.

        Args:
            reason: User's feedback text
            query_hash: Hash of the query being analyzed
            query_sql: Raw SQL (only included if include_query=True)
            plan_json: Execution plan (only included if include_plan=True)
            suggestion_text: What RDST suggested
            sentiment: positive/negative/neutral
            email: Optional email for follow-up
            include_query: Whether to include raw SQL
            include_plan: Whether to include execution plan
            flags_used: CLI flags that were used
        """
        properties = {
            "reason": reason,
            "sentiment": sentiment,
            "has_email": bool(email),
            "include_query": include_query,
            "include_plan": include_plan,
        }

        if query_hash:
            properties["query_hash"] = query_hash
        if suggestion_text:
            properties["suggestion_text"] = suggestion_text
        if flags_used:
            properties["flags_used"] = flags_used
        if include_query and query_sql:
            properties["query_sql"] = query_sql
        if include_plan and plan_json:
            properties["plan_json"] = plan_json

        # Track in PostHog
        self.track_with_stats("feedback_submitted", properties)

        # Notify Slack with full details
        self._slack_notify_feedback(
            reason=reason,
            query_hash=query_hash,
            query_sql=query_sql if include_query else None,
            suggestion_text=suggestion_text,
            sentiment=sentiment,
            email=email,
        )

    # =========================================================================
    # Slack Notifications
    # =========================================================================

    def _slack_notify(self, webhook_url: str, payload: Dict[str, Any]):
        """Send a Slack notification."""
        if not webhook_url:
            return

        requests = _get_requests()
        if not requests:
            return

        def send():
            try:
                requests.post(webhook_url, json=payload, timeout=5)
            except Exception:
                pass

        thread = threading.Thread(target=send, daemon=True)
        thread.start()

    def _slack_notify_install(self, properties: Dict[str, Any]):
        """Notify Slack of a new installation."""
        if not self.SLACK_WEBHOOK_INSTALLS:
            return

        # Get system info directly
        os_name = platform.system()
        os_version = platform.release()
        python_version = platform.python_version()

        payload = {
            "text": f"New RDST Installation",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*New RDST Installation*\n"
                                f"Device: `{self.device_id}`\n"
                                f"OS: {os_name} {os_version}\n"
                                f"Python: {python_version}\n"
                                f"Method: {properties.get('install_method', 'unknown')}"
                    }
                }
            ]
        }

        self._slack_notify(self.SLACK_WEBHOOK_INSTALLS, payload)

    def _slack_notify_feedback(
        self,
        reason: str,
        query_hash: Optional[str],
        query_sql: Optional[str],
        suggestion_text: Optional[str],
        sentiment: str,
        email: Optional[str],
    ):
        """Notify Slack of user feedback."""
        if not self.SLACK_WEBHOOK_FEEDBACK:
            return

        emoji = {"positive": ":+1:", "negative": ":-1:", "neutral": ":neutral_face:"}.get(sentiment, "")

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*RDST Feedback* {emoji}\n"
                            f"Device: `{self.device_id}`\n"
                            f"Sentiment: {sentiment}\n"
                            f"Email: {email or 'not provided'}"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Feedback:*\n{reason[:2000]}"
                }
            }
        ]

        if query_hash:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Query Hash:* `{query_hash}`"}
            })

        if query_sql:
            # Show full query (Slack truncates at ~3000 chars per block anyway)
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Query:*\n```{query_sql[:2000]}```"}
            })

        if suggestion_text:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*RDST Suggestions:*\n{suggestion_text[:1500]}"}
            })

        payload = {
            "text": f"New RDST feedback from {self.device_id}",
            "blocks": blocks
        }

        self._slack_notify(self.SLACK_WEBHOOK_FEEDBACK, payload)

    def _slack_notify_first_analyze(self, target_engine: str, duration_ms: int):
        """Send Slack notification for first successful analyze.

        Note: Query text is intentionally NOT included for privacy.
        Users can explicitly share queries via 'rdst report' if they choose.
        """
        if not self.SLACK_WEBHOOK_ANALYZE:
            return

        payload = {
            "text": f"First successful analyze! Device: {self.device_id}",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"*First Successful Analyze!* :tada:\nDevice: `{self.device_id}`\nEngine: {target_engine}\nDuration: {duration_ms}ms"}
                }
            ]
        }

        self._slack_notify(self.SLACK_WEBHOOK_ANALYZE, payload)

    # =========================================================================
    # Cleanup
    # =========================================================================

    def flush(self):
        """Flush any pending events (call before exit)."""
        posthog = _get_posthog()
        if posthog:
            try:
                posthog.flush()
            except Exception:
                pass
