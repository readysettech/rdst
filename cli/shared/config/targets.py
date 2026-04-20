"""Shared target configuration helpers."""

from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlsplit

import toml

PROXY_TYPES = [
    "none",
    "readyset",
    "proxysql",
    "pgbouncer",
    "tunnel",
    "custom",
]

ENGINES = ["postgresql", "mysql"]


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

    def load(self) -> None:
        if self.path.exists():
            try:
                self._data = toml.load(self.path)
            except Exception:
                self._data = {
                    "targets": {},
                    "default": None,
                    "init": {"completed": False},
                    "llm": {},
                }
        else:
            self._data = {
                "targets": {},
                "default": None,
                "init": {"completed": False},
                "llm": {},
            }

        self._data.setdefault("targets", {})
        self._data.setdefault("default", None)
        self._data.setdefault("init", {"completed": False})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            toml.dump(self._data, f)

    def list_targets(self) -> List[str]:
        return sorted(self._data.get("targets", {}).keys())

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        return (self._data.get("targets", {}) or {}).get(name)

    def upsert(self, name: str, entry: Dict[str, Any]) -> None:
        self._data.setdefault("targets", {})
        self._data["targets"][name] = entry

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

    def get_email(self) -> Optional[str]:
        return self._data.get("email")

    def set_email(self, email: str) -> None:
        """Store or update user email (always overwrites — used by report flow)."""
        if email:
            self._data["email"] = email

    def get_report_token(self) -> Optional[str]:
        """Get the verified report delivery token (keyservice)."""
        return self._data.get("report_token")

    def set_report_token(self, token: str) -> None:
        """Store a verified report_token returned by the keyservice."""
        if token:
            self._data["report_token"] = token

    def get_trial_config(self) -> Dict[str, Any]:
        return self._data.get("trial", {})

    def set_trial_config(self, trial: Dict[str, Any]) -> None:
        self._data["trial"] = trial

    def clear_trial_config(self) -> bool:
        return self._data.pop("trial", None) is not None

    def is_trial_active(self) -> bool:
        trial = self._data.get("trial", {})
        return bool(trial.get("token") and trial.get("status") == "active")

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
