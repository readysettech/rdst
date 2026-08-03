"""Shared target configuration helpers."""

import copy
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlsplit

import toml

from shared.persistence import update_toml

PROXY_TYPES = [
    "none",
    "readyset",
    "proxysql",
    "pgbouncer",
    "tunnel",
    "custom",
]

ENGINES = ["postgresql", "mysql"]

_TARGET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def is_windows_reserved_name(name: str) -> bool:
    return name.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES


def validate_target_name(name: str) -> None:
    """Reject target names that cannot be used as portable path components."""
    if not _TARGET_NAME_RE.fullmatch(name) or name.endswith((".", " ")):
        raise ValueError(
            "Invalid target name. Use 1-64 ASCII letters, numbers, dots, "
            "underscores, or hyphens, starting with a letter or number."
        )
    if is_windows_reserved_name(name):
        raise ValueError(f"Invalid target name '{name}': reserved by Windows")


def _validate_target_mapping(data: dict[str, Any]) -> None:
    folded_names: dict[str, str] = {}
    for name in (data.get("targets") or {}):
        validate_target_name(name)
        folded = name.casefold()
        collision = folded_names.get(folded)
        if collision is not None and collision != name:
            raise ValueError(
                f"Target name '{name}' conflicts with existing target '{collision}'"
            )
        folded_names[folded] = name


def normalize_db_type(db: Optional[str]) -> Optional[str]:
    if db is None:
        return None
    s = db.lower()
    if s in ("postgres", "postgresql", "psql"):
        return "postgresql"
    if s in ("mysql", "mariadb"):
        return "mysql"
    return s


def default_port_for(db: Optional[str]) -> int:
    nd = normalize_db_type(db)
    return 5432 if nd == "postgresql" else 3306


def parse_connection_string(connection_string: str) -> dict:
    """Parse a database connection string into RDST config fields."""
    if not connection_string:
        raise ValueError("Connection string cannot be empty")

    try:
        parsed = urlsplit(connection_string)
    except Exception as e:
        raise ValueError(f"Invalid connection string format: {e}")

    scheme = parsed.scheme.lower()
    if scheme not in ("postgresql", "postgres", "mysql"):
        raise ValueError(
            f"Unsupported database engine '{scheme}'. "
            f"Supported: postgresql, postgres, mysql"
        )

    engine = "postgresql" if scheme in ("postgresql", "postgres") else "mysql"

    if not parsed.hostname:
        raise ValueError("Connection string missing hostname")
    host = parsed.hostname

    port = parsed.port if parsed.port else default_port_for(engine)

    user = unquote(parsed.username) if parsed.username else None
    if not user:
        raise ValueError("Connection string missing username")

    password = unquote(parsed.password) if parsed.password else None

    database = parsed.path.lstrip("/") if parsed.path else None
    if not database:
        raise ValueError("Connection string missing database name")

    ssl_params = {}
    if parsed.query:
        params = parse_qs(parsed.query)
        if "sslmode" in params:
            ssl_params["sslmode"] = params["sslmode"][0]
        if "sslrootcert" in params:
            ssl_params["sslrootcert"] = params["sslrootcert"][0]
        if "sslcert" in params:
            ssl_params["sslcert"] = params["sslcert"][0]
        if "sslkey" in params:
            ssl_params["sslkey"] = params["sslkey"][0]
        if "ssl" in params:
            ssl_params["ssl"] = params["ssl"][0]
        if "ssl-mode" in params:
            ssl_params["ssl-mode"] = params["ssl-mode"][0]
        if "ssl-ca" in params:
            ssl_params["ssl-ca"] = params["ssl-ca"][0]

    tls = False
    if engine == "postgresql":
        sslmode = ssl_params.get("sslmode", "")
        tls = sslmode in ("require", "verify-ca", "verify-full")
    elif engine == "mysql":
        ssl = ssl_params.get("ssl", "")
        ssl_mode = ssl_params.get("ssl-mode", "")
        tls = ssl in ("true", "1") or ssl_mode in (
            "REQUIRED",
            "VERIFY_CA",
            "VERIFY_IDENTITY",
        )

    return {
        "engine": engine,
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "ssl_params": ssl_params,
        "tls": tls,
    }


class TargetsConfig:
    """Simple TOML-based targets storage under ~/.rdst/config.toml."""

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path) if path else Path.home() / ".rdst" / "config.toml"
        self._data: Dict[str, Any] = {
            "targets": {},
            "default": None,
            "init": {"completed": False},
        }
        self._baseline = copy.deepcopy(self._data)

    def load(self) -> None:
        if self.path.exists():
            try:
                self._data = toml.load(self.path)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load RDST config at {self.path}: {exc}"
                ) from exc
        else:
            self._data = {
                "targets": {},
                "default": None,
                "init": {"completed": False},
                "llm": {},
            }

        self._data.setdefault("targets", {})
        self._data.setdefault("ssh_hosts", {})
        self._data.setdefault("default", None)
        self._data.setdefault("init", {"completed": False})
        self._baseline = copy.deepcopy(self._data)

    def save(self) -> None:
        """Merge this instance's changes and atomically persist the config."""
        self._data = update_toml(
            self.path,
            self._baseline,
            self._data,
            validate=_validate_target_mapping,
        )
        self._baseline = copy.deepcopy(self._data)

    def list_targets(self) -> List[str]:
        return sorted(self._data.get("targets", {}).keys())

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        return (self._data.get("targets", {}) or {}).get(name)

    def list_ssh_hosts(self) -> List[str]:
        """Return reusable SSH jump-host profile names."""
        return sorted((self._data.get("ssh_hosts", {}) or {}).keys())

    def get_ssh_host(self, name: str) -> Optional[Dict[str, Any]]:
        """Return one reusable SSH jump-host profile."""
        return (self._data.get("ssh_hosts", {}) or {}).get(name)

    def remember_ssh_host(self, host: str, port: int, user: Optional[str]) -> bool:
        """Remember a successful jump host, deduplicated by host, port, and user."""
        profiles = self._data.setdefault("ssh_hosts", {})
        entry = {"host": host, "port": int(port)}
        if user:
            entry["user"] = user

        for name, existing in profiles.items():
            if (
                existing.get("host") == host
                and int(existing.get("port", 22)) == int(port)
                and (existing.get("user") or None) == user
            ):
                if existing == entry:
                    return False
                profiles[name] = entry
                return True

        base_name = host
        if base_name in profiles:
            base_name = f"{host}-{user}" if user else f"{host}-2"
        name = base_name
        suffix = 2
        while name in profiles:
            name = f"{base_name}-{suffix}"
            suffix += 1
        profiles[name] = entry
        return True

    def upsert(self, name: str, entry: Dict[str, Any]) -> None:
        validate_target_name(name)
        targets = self._data.setdefault("targets", {})
        collision = next(
            (existing for existing in targets if existing != name and existing.casefold() == name.casefold()),
            None,
        )
        if collision is not None:
            raise ValueError(
                f"Target name '{name}' conflicts with existing target '{collision}'"
            )
        targets[name] = entry

    def remove(self, name: str) -> bool:
        targets = self._data.get("targets", {})
        if name in targets:
            del targets[name]
            if self._data.get("default") == name:
                self._data["default"] = None
            return True
        return False

    def set_default(self, name: Optional[str]) -> None:
        self._data["default"] = name

    def get_default(self) -> Optional[str]:
        return self._data.get("default")

    def is_init_completed(self) -> bool:
        try:
            return bool((self._data.get("init") or {}).get("completed", False))
        except Exception:
            return False

    def mark_init_completed(self, version: Optional[str] = None) -> None:
        import datetime

        self._data.setdefault("init", {})
        self._data["init"]["completed"] = True
        self._data["init"]["completed_at"] = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        if version is not None:
            self._data["init"]["version"] = version

    def get_llm_config(self) -> Dict[str, Any]:
        return self._data.get("llm", {})

    def set_llm_config(self, config: Dict[str, Any]) -> None:
        self._data.setdefault("llm", {})
        self._data["llm"].update(config)

    def get_llm_provider(self) -> Optional[str]:
        return self._data.get("llm", {}).get("provider")

    def get_llm_base_url(self) -> Optional[str]:
        return self._data.get("llm", {}).get("base_url")

    def get_llm_model(self) -> Optional[str]:
        return self._data.get("llm", {}).get("model")

    def set_llm_provider(
        self, provider: str, base_url: Optional[str] = None, model: Optional[str] = None
    ) -> None:
        self._data.setdefault("llm", {})
        self._data["llm"]["provider"] = provider
        if base_url:
            self._data["llm"]["base_url"] = base_url
        if model:
            self._data["llm"]["model"] = model

    def set_provider_identity(self, provider: str, identity: Dict[str, Any]) -> None:
        """Store non-secret account pointers without promoting an email.

        Provider emails are analytics context only and deliberately stay out
        of ``[[emails]]``, whose verified flag means mailbox ownership.
        """
        allowed = {
            key: identity[key]
            for key in ("email", "name", "email_verified")
            if identity.get(key) is not None
        }
        if allowed:
            self._data.setdefault("provider_identity", {})[provider] = allowed

    def get_provider_identities(self) -> Dict[str, Dict[str, Any]]:
        identities = self._data.get("provider_identity") or {}
        return identities if isinstance(identities, dict) else {}

    # ------------------------------------------------------------------
    # Email + verified-delivery storage.
    #
    # On disk:
    #
    #   [identity]
    #   first_name = "Ada"
    #   last_name = "Lovelace"
    #
    #   [[emails]]
    #   email = "user@example.com"
    #   primary = true
    #   verified = true
    #   report_token = "..."
    #   verified_at = "2026-..."
    #
    # Exactly one entry is `primary = true`. That entry's email is the user's
    # identity for telemetry and the default report recipient. The machine's
    # one human name lives in [identity] and rides with whichever email is
    # primary on telemetry.
    # ------------------------------------------------------------------

    def _emails_list(self) -> List[Dict[str, Any]]:
        emails = self._data.get("emails")
        if not isinstance(emails, list):
            self._data["emails"] = []
            return self._data["emails"]
        return emails

    def get_email(self) -> Optional[str]:
        """Primary email — telemetry identity AND default report recipient."""
        emails = self._emails_list()
        for entry in emails:
            if entry.get("primary") and entry.get("email"):
                return entry["email"]
        if emails:
            return emails[0].get("email")
        return None

    def set_email(self, email: str) -> None:
        """Promote `email` to primary. Add as an unverified entry if missing.
        Does NOT mark verified — verification happens via add_verified_email."""
        if not email:
            return
        emails = self._emails_list()
        found = False
        for entry in emails:
            if entry.get("email") == email:
                entry["primary"] = True
                found = True
            else:
                entry.pop("primary", None)
        if not found:
            emails.append({"email": email, "primary": True, "verified": False})

    def set_name(self, first_name: str, last_name: str) -> None:
        """Store the machine's one human name, in the top-level [identity]
        table. Per machine, not per email: swapping between several emails
        keeps the same person. Blank parts leave the stored value alone."""
        identity = self._data.setdefault("identity", {})
        if first_name:
            identity["first_name"] = first_name
        if last_name:
            identity["last_name"] = last_name

    def get_identity(self) -> Dict[str, Any]:
        """The machine identity: primary email (with verified state) plus the
        machine-level name."""
        identity = self._data.get("identity") or {}
        emails = self._emails_list()
        primary = next((e for e in emails if e.get("primary")), None)
        if primary is None and emails:
            primary = emails[0]
        return {
            "email": primary.get("email") if primary else None,
            "first_name": identity.get("first_name"),
            "last_name": identity.get("last_name"),
            "verified": bool(primary.get("verified")) if primary else False,
        }

    def set_primary_email(self, email: str) -> bool:
        """Mark an existing entry as primary. Returns False if not found."""
        emails = self._emails_list()
        found = False
        for entry in emails:
            if entry.get("email") == email:
                entry["primary"] = True
                found = True
            else:
                entry.pop("primary", None)
        return found

    def add_verified_email(self, email: str, report_token: str) -> None:
        """Mark `email` as verified with a report_token. Adds the entry if
        missing. Does not change which entry is primary unless this is the
        very first email."""
        if not email or not report_token:
            return
        import datetime

        now_iso = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        emails = self._emails_list()
        for entry in emails:
            if entry.get("email") == email:
                entry["verified"] = True
                entry["report_token"] = report_token
                entry.setdefault("verified_at", now_iso)
                return
        new_entry: Dict[str, Any] = {
            "email": email,
            "verified": True,
            "report_token": report_token,
            "verified_at": now_iso,
        }
        if not any(e.get("primary") for e in emails):
            new_entry["primary"] = True
        emails.append(new_entry)

    def get_token_for_email(self, email: str) -> Optional[str]:
        """Token for `email` if it has a verified entry. Else None."""
        if not email:
            return None
        for entry in self._emails_list():
            if (
                entry.get("email") == email
                and entry.get("verified")
                and entry.get("report_token")
            ):
                return entry.get("report_token")
        return None

    def has_verified_email(self, email: str) -> bool:
        return self.get_token_for_email(email) is not None

    def list_verified_emails(self) -> List[str]:
        """Verified email addresses, primary first, then in file order."""
        verified: List[str] = []
        primary: Optional[str] = None
        for entry in self._emails_list():
            if not (entry.get("verified") and entry.get("report_token")):
                continue
            addr = entry.get("email")
            if not addr:
                continue
            if entry.get("primary"):
                primary = addr
            else:
                verified.append(addr)
        return ([primary] if primary else []) + verified

    def remove_email(self, email: str) -> bool:
        """Drop an entry — used to recover from stale/invalid tokens. If the
        primary is removed, promotes the next remaining entry."""
        if not email:
            return False
        emails = self._emails_list()
        new_emails = [e for e in emails if e.get("email") != email]
        if len(new_emails) == len(emails):
            return False
        if not any(e.get("primary") for e in new_emails) and new_emails:
            new_emails[0]["primary"] = True
        self._data["emails"] = new_emails
        return True

    # Backward-compat shims. Old callers asked for "the" report token. Now
    # tokens are per-email, so these resolve against the primary entry.

    def get_report_token(self) -> Optional[str]:
        primary = self.get_email()
        return self.get_token_for_email(primary) if primary else None

    def set_report_token(self, token: str) -> None:
        primary = self.get_email()
        if primary and token:
            self.add_verified_email(primary, token)

    def get_trial_config(self) -> Dict[str, Any]:
        return self._data.get("trial", {})

    def set_trial_config(self, trial: Dict[str, Any]) -> None:
        self._data["trial"] = trial

    def clear_trial_config(self) -> bool:
        return self._data.pop("trial", None) is not None

    def is_trial_active(self) -> bool:
        trial = self._data.get("trial", {})
        return bool(trial.get("token") and trial.get("status") == "active")

    # ------------------------------------------------------------------
    # QueryPilot web-demo state.
    #
    # The demo keeps its whole persistent footprint in a single [demo]
    # section (ports, discovery mode, cache budget, cron flags, auto-teardown
    # deadline) so it needs no extra files under ~/.rdst. It is otherwise
    # fully separate from targets and other rdst features.
    # ------------------------------------------------------------------

    def get_demo_state(self) -> Dict[str, Any]:
        return self._data.get("demo", {}) or {}

    def set_demo_state(self, state: Dict[str, Any]) -> None:
        self._data["demo"] = state

    def clear_demo_state(self) -> bool:
        return self._data.pop("demo", None) is not None

    # The demo walkthrough is per-install, not per-demo-run: it lives in its
    # own [demo_tour] section so demo teardown (which clears [demo]) leaves
    # it intact, and it resets only when ~/.rdst itself is wiped.

    def is_demo_tour_done(self) -> bool:
        return bool((self._data.get("demo_tour") or {}).get("done"))

    def mark_demo_tour_done(self) -> None:
        self._data["demo_tour"] = {"done": True}

    def list_targets_by_group(self, group: str) -> List[str]:
        targets = self._data.get("targets", {})
        return sorted(name for name, cfg in targets.items() if cfg.get("group") == group)

    def list_targets_by_tag(self, tag: str) -> List[str]:
        targets = self._data.get("targets", {})
        return sorted(
            name for name, cfg in targets.items() if tag in (cfg.get("tags") or [])
        )

    def list_fleet_targets(
        self,
        group: Optional[str] = None,
        tag: Optional[str] = None,
        exclude: Optional[List[str]] = None,
    ) -> List[str]:
        targets = self._data.get("targets", {})
        exclude_set = set(exclude or [])
        result = []
        for name, cfg in targets.items():
            target_type = cfg.get("target_type", "database")
            if target_type == "readyset":
                continue
            proxy = cfg.get("proxy", "none")
            if proxy == "readyset":
                continue
            if name in exclude_set:
                continue
            if group and cfg.get("group") != group:
                continue
            if tag and tag not in (cfg.get("tags") or []):
                continue
            result.append(name)
        return sorted(result)

    def list_groups(self) -> List[str]:
        targets = self._data.get("targets", {})
        groups = set()
        for cfg in targets.values():
            group = cfg.get("group")
            if group:
                groups.add(group)
        return sorted(groups)

    def list_tags(self) -> List[str]:
        targets = self._data.get("targets", {})
        tags = set()
        for cfg in targets.values():
            for tag in cfg.get("tags") or []:
                tags.add(tag)
        return sorted(tags)


def get_targets_config_class():
    """Return the live TargetsConfig class."""
    return TargetsConfig


def create_targets_config(*args, **kwargs) -> TargetsConfig:
    """Instantiate the live TargetsConfig class."""
    return get_targets_config_class()(*args, **kwargs)
