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

import json
import os
import platform
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from shared.constants import rdst_data_dir

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


@dataclass
class TerminalState:
    """Outcome captured from a terminal SSE event by a `TerminalDetector`.

    `extra` carries command-specific properties (e.g. `query_hash` for
    analyze, `row_count` for ask) that flow into the PostHog event.
    """

    success: bool
    error_type: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


# A detector inspects a single SSE event and returns a `TerminalState` if the
# event marks the end of the run, or `None` for progress/intermediate events.
# Each feature provides its own detector, using `isinstance` checks against
# its own event dataclasses — no magic-string `event.type` matching.
TerminalDetector = Callable[[Any], Optional[TerminalState]]


@dataclass
class CommandRun:
    """Mutable state captured during an SSE command run.

    Use via `TelemetryManager.command_run` (async) or `command_run_sync`. The
    caller mutates fields as the run progresses (or sets `success`/`extra`
    explicitly); on exit, the context manager emits the `<name>_run`
    PostHog event (and, for `analyze`, the `first_analyze` event on first
    success).

    `observe(event)` is a convenience for SSE generators: it delegates to
    the per-command `TerminalDetector` provided at construction. If no
    detector is configured, `observe` is a no-op — set fields directly.
    """

    name: str
    source: str
    target_engine: str = "unknown"
    mode: Optional[str] = None
    success: Optional[bool] = None
    error_type: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    _start: float = field(default_factory=time.time)
    _detector: Optional[TerminalDetector] = None

    @property
    def duration_ms(self) -> int:
        return int((time.time() - self._start) * 1000)

    def observe(self, event: Any) -> None:
        """Apply the configured detector to a streaming event.

        Non-terminal events are ignored. Terminal failures override prior
        success state; `error_type` and `extra` only fill if not already set
        by the caller, so explicit assignments win.
        """
        if self._detector is None:
            return
        state = self._detector(event)
        if state is None:
            return
        # Failure dominates: a later success cannot overwrite an earlier failure.
        if self.success is None or state.success is False:
            self.success = state.success
        if state.error_type and self.error_type is None:
            self.error_type = state.error_type
        for key, value in state.extra.items():
            if value is not None and self.extra.get(key) is None:
                self.extra[key] = value

    def error(self, exc: BaseException) -> None:
        """Mark this run as failed due to an exception, tagging the type."""
        self.success = False
        if self.error_type is None:
            self.error_type = type(exc).__name__


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
    # PostHog write-only ingest key — safe to embed (cannot read data, only write events)
    # Override via RDST_POSTHOG_KEY env var if needed
    # RDST_SENTRY_DSN: Sentry DSN for crash reporting
    # RDST_SLACK_WEBHOOK_*: Slack webhooks for notifications
    POSTHOG_API_KEY = os.environ.get("RDST_POSTHOG_KEY", "phc_WPINnbS1CUiADz01QFeDZCr4Wn7jXfNPxe1EK0V2ZzP")
    POSTHOG_HOST = "https://us.i.posthog.com"
    SENTRY_DSN = os.environ.get("RDST_SENTRY_DSN", "")
    SLACK_WEBHOOK_INSTALLS = os.environ.get("RDST_SLACK_WEBHOOK_INSTALLS", "")
    SLACK_WEBHOOK_FEEDBACK = os.environ.get("RDST_SLACK_WEBHOOK_FEEDBACK", "")
    SLACK_WEBHOOK_ANALYZE = os.environ.get("RDST_SLACK_WEBHOOK_ANALYZE", "")

    def __init__(self):
        self._device_id: Optional[str] = None
        self._enabled: Optional[bool] = None
        self._initialized = False
        self._stats: Optional[Dict[str, int]] = None
        self._rdst_dir = rdst_data_dir()
        self._lock = threading.Lock()
        # Per-command finalizer dispatch table. Bound methods, so they
        # close over `self` correctly. Features can extend via
        # `register_command_finalizer`.
        self._command_finalizers: Dict[str, Callable[["CommandRun"], None]] = {
            "analyze": self._analyze_finalizer,
        }

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

    def _get_auth_type(self) -> str:
        """Determine how the user is authenticating LLM requests.

        Returns one of: 'own_key', 'trial', 'none'.
        """
        try:
            if os.getenv("ANTHROPIC_API_KEY"):
                return "own_key"
            if os.getenv("RDST_TRIAL_TOKEN"):
                return "trial"
            config_file = self._rdst_dir / "config.toml"
            if config_file.exists():
                content = config_file.read_text()
                if "[trial]" in content:
                    if 'status = "active"' in content:
                        return "trial"
                    if 'status = "exhausted"' in content:
                        return "trial_exhausted"
            # Check keyring (fast path only — don't probe slow backends)
            try:
                from shared.secret_store_service import SecretStoreService
                store = SecretStoreService()
                if store.get_secret("ANTHROPIC_API_KEY"):
                    return "own_key"
                if store.get_secret("RDST_TRIAL_TOKEN"):
                    return "trial"
            except Exception:
                pass
        except Exception:
            pass
        return "none"

    def _get_base_properties(self) -> Dict[str, Any]:
        """Get base properties included with every event."""
        return {
            "device_id": self.device_id,
            "rdst_version": self._get_version(),
            "os": platform.system(),
            "os_version": platform.release(),
            "python_version": platform.python_version(),
            "auth_type": self._get_auth_type(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
            "first_seen": datetime.now(timezone.utc).isoformat(),
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
                    # If email is in properties, identify the user so PostHog
                    # links this device to the email across sessions/devices.
                    email = all_props.get("email")
                    if email and email != "unknown":
                        try:
                            posthog.identify(
                                distinct_id=self.device_id,
                                properties={"email": email},
                            )
                        except Exception:
                            pass
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

    def track_first_event_once(
        self,
        event: str,
        flag: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Fire `event` exactly once per device, gated by stats `flag`.

        Used for "first X" Slack alerts (first_audit, first_fleet_audit,
        etc.) where the per-device gate is a boolean flag in stats.json
        rather than a counter check. Returns True if the event was
        fired, False if it had already fired previously.

        The `first_analyze` event uses a different gate (counter equals
        one) and stays inside `track_analyze`.
        """
        if self._get_stats().get(flag):
            return False
        self.track(event, properties)
        self._increment_stat(flag, 1)
        return True

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

    @staticmethod
    def _is_interactive() -> bool:
        """Check if we're in an interactive terminal with a real human.

        Returns False in CI, piped stdin, non-TTY, or when explicitly disabled.
        """
        if not sys.stdin.isatty():
            return False
        # Explicit opt-out (set by integration tests and CI scripts)
        if os.environ.get("RDST_NON_INTERACTIVE"):
            return False
        # Common CI environments that provide pseudo-TTYs
        for var in ("BUILDKITE", "CI", "GITHUB_ACTIONS", "JENKINS_URL", "GITLAB_CI"):
            if os.environ.get(var):
                return False
        return True

    def show_nps_prompt(self) -> bool:
        """
        Show the NPS prompt and handle response.
        Returns True if user responded, False if skipped.
        """
        if not self._is_interactive():
            return False

        from shared.ui import get_console, StyledPanel, StyleTokens

        console = get_console()

        try:
            console.print()
            console.print(StyledPanel.create(
                "How's RDST working for you?\n"
                "[bold][1][/bold] Great    [bold][2][/bold] Not great    [bold][3][/bold] Skip",
                title="Quick Feedback",
            ))

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
                self.track("nps_response", {"rating": "positive", "score": 1})
                console.print(f"[{StyleTokens.SUCCESS}]Thanks![/{StyleTokens.SUCCESS}]")
                return True

            elif response == "2":
                console.print("\nWhat can we improve?")
                feedback = ""
                try:
                    feedback = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    pass

                email = ""
                if feedback:
                    console.print("Email so we can follow up? (Enter to skip)")
                    try:
                        email = input("> ").strip()
                    except (EOFError, KeyboardInterrupt):
                        pass

                feedback = feedback or "No details provided"

                self.track("nps_response", {
                    "rating": "negative",
                    "score": 0,
                    "feedback": feedback,
                    "email": email or None,
                })
                self._slack_notify_first_analyze_feedback("negative", feedback, email or None)
                console.print(f"[{StyleTokens.SUCCESS}]Thanks for the feedback! We'll work on it.[/{StyleTokens.SUCCESS}]")
                return True

            else:
                self.track("nps_response", {"rating": "skipped"})
                return False

        except (EOFError, KeyboardInterrupt):
            return False

    # =========================================================================
    # First Analyze Feedback (one-time prompt after first successful analyze)
    # =========================================================================

    def is_first_successful_analyze(self) -> bool:
        """Check if the most recent analyze was the user's first success.

        Call this AFTER track_analyze() — it checks if successful_analyzes == 1
        (i.e., track_analyze just incremented it from 0 to 1).
        """
        stats = self._get_stats()
        return stats.get("successful_analyzes", 0) == 1 and not stats.get("first_analyze_feedback_shown", False)

    def show_first_analyze_feedback(self) -> bool:
        """Show micro-feedback prompt after the user's first successful analyze.

        Returns True if user responded, False if skipped.
        Sends results to both PostHog and Slack.
        """
        if not self._is_interactive():
            return False

        from shared.ui import get_console, StyledPanel, StyleTokens

        console = get_console()

        try:
            console.print()
            console.print(StyledPanel.create(
                "Your first analysis is complete!\n"
                "Was this helpful?  [bold][1][/bold] Yes  [bold][2][/bold] No  [bold][3][/bold] Skip",
                title="Quick Feedback",
                variant="success",
            ))

            response = input("> ").strip()

            # Mark as shown so we never ask again
            stats = self._get_stats()
            stats["first_analyze_feedback_shown"] = True
            self._save_stats()

            if response == "1":
                self.track("first_analyze_feedback", {"rating": "positive"})
                self._slack_notify_first_analyze_feedback("positive", None, None)
                console.print(f"[{StyleTokens.SUCCESS}]Glad to hear it![/{StyleTokens.SUCCESS}]")
                return True

            elif response == "2":
                console.print("\nWhat could be better?")
                feedback = ""
                try:
                    feedback = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    pass

                email = ""
                if feedback:
                    console.print("Email so we can follow up? (Enter to skip)")
                    try:
                        email = input("> ").strip()
                    except (EOFError, KeyboardInterrupt):
                        pass

                feedback = feedback or "No details provided"
                self.track("first_analyze_feedback", {
                    "rating": "negative",
                    "feedback": feedback,
                    "email": email or None,
                })
                self._slack_notify_first_analyze_feedback("negative", feedback, email or None)
                console.print(f"[{StyleTokens.SUCCESS}]Thanks for the feedback -- we'll work on it.[/{StyleTokens.SUCCESS}]")
                return True

            else:
                self.track("first_analyze_feedback", {"rating": "skipped"})
                return False

        except (EOFError, KeyboardInterrupt):
            # Mark as shown even if interrupted
            stats = self._get_stats()
            stats["first_analyze_feedback_shown"] = True
            self._save_stats()
            return False

    def _slack_notify_first_analyze_feedback(
        self, sentiment: str, feedback: Optional[str], email: Optional[str]
    ):
        """Send first-analyze feedback to Slack."""
        if not self.SLACK_WEBHOOK_FEEDBACK:
            return

        emoji = ":+1:" if sentiment == "positive" else ":-1:"

        text = (
            f"*First Analyze Feedback* {emoji}\n"
            f"Device: `{self.device_id}`\n"
            f"Rating: {sentiment}"
        )
        if feedback:
            text += f"\nFeedback: {feedback[:1500]}"
        if email:
            text += f"\nEmail: {email}"

        payload = {
            "text": f"First analyze feedback from {self.device_id}",
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                }
            ],
        }

        self._slack_notify(self.SLACK_WEBHOOK_FEEDBACK, payload)

    # =========================================================================
    # Specific Event Trackers
    # =========================================================================

    def track_installation(self, install_method: str = "unknown"):
        """Track a new installation."""
        self._increment_stat("installations", 1)

        properties = {
            "display_name": "RDST Installed",
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
        source: str = "cli",  # cli, web
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
            "source": source,
        }
        if error_type:
            properties["error_type"] = error_type

        self.track_with_stats("analyze_run", properties)

        # Track first successful analyze as a separate event for Slack alerts
        if is_first_success:
            first_analyze_props: Dict[str, Any] = {
                "display_name": "RDST First Analyze",
                "target_engine": target_engine,
                "duration_ms": duration_ms,
                "source": source,
            }
            # Include email if available (from trial signup, feedback, etc.)
            try:
                from shared.config.targets import create_targets_config

                cfg = create_targets_config(path=str(self._rdst_dir / "config.toml"))
                cfg.load()
                email = cfg.get_email()
                if not email:
                    # Fall back to trial config email
                    email = cfg.get_trial_config().get("email")
                if email:
                    first_analyze_props["email"] = email
                    first_analyze_props["email_domain"] = email.split("@")[1] if "@" in email else "unknown"
            except Exception:
                pass
            self.track("first_analyze", first_analyze_props)
            self._slack_notify_first_analyze(target_engine, duration_ms)

    # Per-command stat keys (incremented by `_generic_finalizer`).
    # Commands with bespoke finalizers manage their own counters.
    # Note: `top` and `top_realtime` share `total_top_runs` so the NPS
    # prompt pacing reflects all top usage; their PostHog events stay
    # separate (`top_run` vs `top_realtime_run`) since they have different
    # success semantics and lifecycles.
    _COMMAND_STAT_KEYS: Dict[str, str] = {
        "ask": "total_asks",
        "scan": "total_scans",
        "top": "total_top_runs",
        "top_realtime": "total_top_runs",
    }

    def _analyze_finalizer(self, run: "CommandRun") -> None:
        """Bespoke finalizer for analyze.

        Routes through `track_analyze` to preserve `first_analyze` PostHog
        event, the Slack alert on first success, and `successful_analyzes`
        bookkeeping.
        """
        self.track_analyze(
            query_hash=str(run.extra.get("query_hash") or "unknown"),
            mode=run.mode or "standard",
            duration_ms=run.duration_ms,
            success=bool(run.success),
            error_type=run.error_type,
            target_engine=run.target_engine,
            source=run.source,
        )

    def _generic_finalizer(self, run: "CommandRun") -> None:
        """Default finalizer: increment `_COMMAND_STAT_KEYS[name]` (if any)
        and emit `<name>_run` via `track_with_stats`. Used for ask/scan/top
        and any command that registers no bespoke finalizer."""
        stat_key = self._COMMAND_STAT_KEYS.get(run.name)
        if stat_key:
            self._increment_stat(stat_key, 1)

        properties: Dict[str, Any] = {
            "source": run.source,
            "target_engine": run.target_engine,
            "duration_ms": run.duration_ms,
            "success": bool(run.success),
        }
        if run.mode:
            properties["mode"] = run.mode
        if run.error_type:
            properties["error_type"] = run.error_type
        for key, value in run.extra.items():
            if value is not None:
                properties[key] = value

        self.track_with_stats(f"{run.name}_run", properties)

    def register_command_finalizer(
        self,
        name: str,
        finalizer: Callable[["CommandRun"], None],
    ) -> None:
        """Register a bespoke finalizer for a command.

        Useful when a command has side events beyond the generic
        `<name>_run` event (e.g. analyze's `first_analyze`). Most commands
        do not need this — they fall through to `_generic_finalizer`.
        """
        self._command_finalizers[name] = finalizer

    def _finalize_command_run(self, run: "CommandRun") -> None:
        """Dispatch to the registered finalizer for `run.name`, falling
        back to `_generic_finalizer`. Telemetry never breaks the caller —
        all errors here are swallowed.
        """
        try:
            finalizer = self._command_finalizers.get(run.name, self._generic_finalizer)
            finalizer(run)
        except Exception:
            pass

    @contextmanager
    def command_run_sync(
        self,
        name: str,
        *,
        source: str,
        target_engine: str = "unknown",
        mode: Optional[str] = None,
        terminal_detector: Optional[TerminalDetector] = None,
        **extra: Any,
    ):
        """Sync context manager for tracking an SSE-style command (CLI side).

        Inside the `with` block, the caller can either mutate `run.success`/
        `run.error_type`/`run.extra` directly, or call `run.observe(event)`
        with a `terminal_detector` configured. On exit (clean or via
        exception), `<name>_run` is emitted via `_finalize_command_run`.
        Unhandled exceptions set `success=False` and `error_type` to the
        exception class name, then re-raise.
        """
        run = CommandRun(
            name=name,
            source=source,
            target_engine=target_engine,
            mode=mode,
            extra=dict(extra),
            _detector=terminal_detector,
        )
        try:
            yield run
        except BaseException as e:
            run.error(e)
            raise
        finally:
            self._finalize_command_run(run)

    @asynccontextmanager
    async def command_run(
        self,
        name: str,
        *,
        source: str,
        target_engine: str = "unknown",
        mode: Optional[str] = None,
        terminal_detector: Optional[TerminalDetector] = None,
        **extra: Any,
    ):
        """Async sibling of `command_run_sync` for SSE/streaming endpoints."""
        run = CommandRun(
            name=name,
            source=source,
            target_engine=target_engine,
            mode=mode,
            extra=dict(extra),
            _detector=terminal_detector,
        )
        try:
            yield run
        except BaseException as e:
            run.error(e)
            raise
        finally:
            self._finalize_command_run(run)

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

    def track_audit_report(
        self,
        target: str,
        engine: str = "unknown",
        has_email: bool = False,
        has_readyset_testing: bool = False,
        queries_cached: int = 0,
        avg_speedup: float = 0.0,
    ):
        """Track audit report generation (PLG Advanced Flow completion signal)."""
        self._increment_stat("total_audit_reports", 1)
        properties = {
            "target": target,
            "engine": engine,
            "email_collected": has_email,
            "readyset_tested": has_readyset_testing,
            "queries_cached": queries_cached,
            "avg_speedup": round(avg_speedup, 1),
            "report_format": "html",
            "flow_stage": "advanced",
        }
        self.track_with_stats("audit_report_generated", properties)

    def track_advanced_flow_complete(
        self,
        target: str,
        engine: str = "unknown",
        report_sent: bool = False,
        readyset_tested: bool = False,
    ):
        """Track completion of the PLG Advanced Flow."""
        properties = {
            "target": target,
            "engine": engine,
            "report_sent": report_sent,
            "readyset_tested": readyset_tested,
            "flow_stage": "advanced",
        }
        self.track("advanced_flow_complete", properties)

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
            "email": email,
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

        # Save email for enriching future telemetry events (first_analyze, etc.)
        if email:
            try:
                from shared.config.targets import create_targets_config

                cfg = create_targets_config(path=str(self._rdst_dir / "config.toml"))
                cfg.load()
                if not cfg.get_email():
                    cfg.set_email(email)
                    cfg.save()
            except Exception:
                pass

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
