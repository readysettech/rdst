"""Traces of a database in any text: connection URLs, a managed provider's host names,
command lines, secret managers and the drivers a project depends on.

The scan reads project files, agent session logs and shell history with these, whatever
language or framework the project uses. Nothing here keeps a password: a finding says
whether its text had one and where, and RDST reads it again only when the user connects.
"""

from __future__ import annotations

import re
from typing import Any, Iterator
from urllib.parse import quote, unquote, urlsplit

URL_SCHEMES = {"postgres": "postgresql", "postgresql": "postgresql", "mysql": "mysql",
               "mariadb": "mysql", "mysql2": "mysql", "mysqlx": "mysql", "pgsql": "postgresql",
               "cockroachdb": "postgresql"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}

# Managed databases by their host names. Cloud SQL and AlloyDB connect by IP or through
# the proxy, so they're known by their instance connection names instead.
PROVIDER_HOSTS: tuple[tuple[str, str], ...] = (
    (r"\.supabase\.(co|com)$|\.pooler\.supabase\.com$", "supabase"),
    (r"\.neon\.tech$", "neon"),
    (r"\.ondigitalocean\.com$|\.db\.ondigitalocean\.com$", "digitalocean"),
    (r"\.rds\.amazonaws\.com(\.cn)?$", "aws"),
    (r"^ec2-[\d-]+\.compute-1\.amazonaws\.com$", "heroku"),
    (r"\.(postgres|mysql)\.database\.azure\.com$|\.postgres\.cosmos\.azure\.com$", "azure"),
    (r"\.psdb\.cloud$|\.connect\.psdb\.cloud$", "planetscale"),
    (r"\.railway\.(app|internal)$|\.rlwy\.net$", "railway"),
    (r"\.render\.com$", "render"),
    (r"\.aivencloud\.com$", "aiven"),
    (r"\.timescale\.com$|\.tsdb\.cloud\.timescale\.com$", "timescale"),
    (r"\.postgresbridge\.com$", "crunchy"),
    (r"\.cockroachlabs\.cloud$", "cockroach"),
    (r"\.xata\.sh$", "xata"),
    (r"\.tembo\.io$", "tembo"),
    (r"\.thenile\.dev$", "nile"),
    (r"^db\.prisma\.io$|\.prisma-data\.net$", "prisma"),
    (r"\.tidbcloud\.com$", "tidb"),
    (r"\.singlestore\.com$|\.svc\.singlestore\.com$", "singlestore"),
    (r"\.flycast$|\.fly\.dev$", "fly"),
    (r"\.vultrdb\.com$", "vultr"),
    (r"\.linodedb\.net$", "linode"),
    (r"\.scw\.cloud$", "scaleway"),
    (r"\.database\.cloud\.ovh\.(net|us)$", "ovh"),
    (r"\.cloudsql\.|\.sql\.goog$", "gcp"),
)
PROVIDER_PATTERNS = tuple((re.compile(pattern), name) for pattern, name in PROVIDER_HOSTS)
PROVIDERS = tuple(dict.fromkeys(name for _, name in PROVIDER_HOSTS))
# The provider host names worth finding without a URL around them.
# Not right after a letter or a backslash, so a log's escaped "\\n" or "\\t" isn't read as
# part of the name.
HOST_TEXT = re.compile(
    r"(?<![\\\w.-])[a-z0-9][a-z0-9.-]{2,200}\.(?:supabase\.co|pooler\.supabase\.com|neon\.tech|"
    r"ondigitalocean\.com|rds\.amazonaws\.com|database\.azure\.com|psdb\.cloud|rlwy\.net|"
    r"render\.com|aivencloud\.com|tsdb\.cloud\.timescale\.com|postgresbridge\.com|"
    r"cockroachlabs\.cloud|tidbcloud\.com|vultrdb\.com|linodedb\.net)\b", re.IGNORECASE)
HOST_TELLS = ("supabase.co", "neon.tech", "ondigitalocean.com", "rds.amazonaws.com",
              "database.azure.com", "psdb.cloud", "rlwy.net", "render.com", "aivencloud.com",
              "timescale.com", "postgresbridge.com", "cockroachlabs.cloud", "tidbcloud.com",
              "vultrdb.com", "linodedb.net")
# A Cloud SQL instance connection name, project:region:instance, where code or config
# names it for the proxy or a connector.
CLOUD_SQL = re.compile(
    r"(?:INSTANCE_CONNECTION_NAME|CLOUD_SQL_CONNECTION_NAME|cloud[_-]?sql[_-]?instance|"
    r"cloudsql/|cloud-sql-proxy|cloud_sql_proxy|instances=)[\"'=:\s/]*"
    r"([a-z][a-z0-9-]{4,61}[a-z0-9]:[a-z]+-[a-z]+\d:[a-z][a-z0-9-]{0,95})")

# What a placeholder looks like: docs and examples name these, real projects don't.
PLACEHOLDER_HOSTS = {"host", "hostname", "your-host", "yourhost", "db-host", "dbhost",
                     "server", "example.com", "example.org", "db.example.com", "x", "xxx",
                     "db.x.supabase.co", "db.xxx.supabase.co", "db.project.supabase.co",
                     "project-ref", "my-host", "myhost", "your_host", "ip", "address"}
PLACEHOLDER = re.compile(r"[<>{}$*]|\.\.\.|your[-_]|xxx|example|placeholder|changeme|"
                         r"abc123|\.region\.|^console\.|dummy|fake|sample", re.IGNORECASE)
PLACEHOLDER_DATABASES = {"dbname", "mydb", "foobar", "database", "db_name", "your_db",
                         "tables", "databases", "test_db_name", "name"}


def provider_of(host: str | None) -> str:
    if not host:
        return "other"
    lower = host.lower().rstrip(".")
    if lower in LOCAL_HOSTS or lower.endswith(".localhost"):
        return "local"
    return next((name for pattern, name in PROVIDER_PATTERNS if pattern.search(lower)),
                "other")


def is_placeholder(host: str) -> bool:
    lower = host.lower()
    if lower in PLACEHOLDER_HOSTS or PLACEHOLDER.search(lower):
        return True
    # A Supabase project's ref is 20 letters and digits; db.x.supabase.co is from a doc.
    match = re.match(r"^db\.([a-z0-9]+)\.supabase\.co$", lower)
    return bool(match and len(match.group(1)) != 20)


def parse_url(value: str) -> dict[str, Any] | None:
    """One database URL's parts, without its password: postgres://, mysql://, the
    driver-qualified forms like postgresql+psycopg://, and JDBC's."""
    value = value.strip().strip("\"'`,;)")
    if value.lower().startswith("jdbc:"):
        value = value[5:]
    scheme, sep, _rest = value.partition("://")
    engine = URL_SCHEMES.get(scheme.split("+", 1)[0].lower())
    if not sep or engine is None:
        return None
    try:
        url = urlsplit(value)
        port = url.port
        host = url.hostname
    except ValueError:
        return None
    if not host or is_placeholder(host) or "%" in host:
        return None
    database = unquote(url.path.lstrip("/").split("?")[0]) or None
    if database and (PLACEHOLDER.search(database) or database.lower() in PLACEHOLDER_DATABASES
                     or not re.fullmatch(r"[\w.-]+", database)):
        database = None
    return {"engine": engine, "host": host, "port": port, "database": database,
            "user": unquote(url.username) if url.username else None,
            "has_password": bool(url.password) and not PLACEHOLDER.search(url.password or "")}


URL_TAIL = re.compile(r"(?:jdbc:)?(postgres(?:ql)?|pgsql|mysql2?|mysqlx|mariadb|cockroachdb)"
                      r"(?:\+[a-z0-9]+)?$", re.IGNORECASE)
URL_REST = re.compile(r"[^\s\"'\\<>`|]{3,300}")


def find_urls(text: str) -> Iterator[dict[str, Any]]:
    """Each database URL in ``text``. It jumps from one "://" to the next, so a long log
    reads fast."""
    index = text.find("://")
    while index != -1:
        tail = URL_TAIL.search(text, max(0, index - 30), index)
        if tail:
            rest = URL_REST.match(text, index + 3)
            if rest:
                parsed = parse_url(text[tail.start():rest.end()])
                if parsed:
                    yield parsed
        index = text.find("://", index + 3)


def find_raw_url(text: str, host: str) -> str | None:
    """The full URL, password included, that names ``host``: read only when the user
    connects that database."""
    index = text.find("://")
    while index != -1:
        tail = URL_TAIL.search(text, max(0, index - 30), index)
        rest = URL_REST.match(text, index + 3) if tail else None
        if rest:
            raw = text[tail.start():rest.end()].strip("\"'`,;)")
            parsed = parse_url(raw)
            if parsed and parsed["has_password"] and \
                    database_host(parsed["host"]).lower() == host.lower():
                return raw[5:] if raw.lower().startswith("jdbc:") else raw
        index = text.find("://", index + 3)
    # A command that names the host and carries its password, like PGPASSWORD=... psql -h.
    for match in reversed(list(COMMAND.finditer(text))):
        found = _command(match, text)
        if found and found["password"] and found["user"] and \
                database_host(found["host"]).lower() == host.lower():
            scheme = "mysql" if found["engine"] == "mysql" else "postgresql"
            port = f":{found['port']}" if found["port"] else ""
            return (f"{scheme}://{quote(found['user'], safe='')}:"
                    f"{quote(found['password'], safe='')}@{found['host']}{port}/"
                    f"{found['database'] or ('postgres' if scheme == 'postgresql' else '')}")
    return None


# .NET and ODBC style: Host=...;Database=...;Username=... or Server=...;Uid=...
KEYVALUE = re.compile(r"(?:^|[\"';\s])((?:Host|Server|Data Source)\s*=\s*[^;\"'\n]+;"
                      r"[^\"'\n]{0,400})", re.IGNORECASE)


def find_keyvalue(text: str) -> Iterator[dict[str, Any]]:
    for match in KEYVALUE.finditer(text):
        parts = {}
        for piece in match.group(1).split(";"):
            key, sep, value = piece.partition("=")
            if sep:
                parts[key.strip().lower().replace(" ", "")] = value.strip()
        host = parts.get("host") or parts.get("server") or parts.get("datasource")
        database = parts.get("database") or parts.get("initialcatalog")
        if not host or not database or is_placeholder(host):
            continue
        host, _, port = host.replace("tcp:", "").partition(",")
        port = port or parts.get("port", "")
        user = parts.get("username") or parts.get("userid") or parts.get("uid") \
            or parts.get("user")
        mysql = "uid" in parts or "sslmode" not in parts and port == "3306"
        yield {"engine": "mysql" if mysql else "postgresql", "host": host.strip(),
               "port": int(port) if port.strip().isdigit() else None, "database": database,
               "user": user, "has_password": bool(parts.get("password") or parts.get("pwd"))}


# psql -h host -p 5432 -U user -d db, mysql -h host -P 3306 -u user db, and pg_dump's kin.
COMMAND = re.compile(r"\b(psql|pg_dump|pg_restore|pgcli|mysql|mysqldump|mycli)\s+([^\n|;&]{3,300})")


# The password a command line carries: PGPASSWORD=... or MYSQL_PWD=... set just before it,
# or mysql's -p<password> and --password=<password>.
PASSWORD_ENV = re.compile(r"\b(?:PGPASSWORD|MYSQL_PWD)=(\"[^\"\n]*\"|'[^'\n]*'|[^\s;&|]+)"
                          r"(?:\s+[A-Z_][A-Z0-9_]*=\S*)*\s+(?:env\s+)?$")
MYSQL_PASSWORD = re.compile(r"(?:^|\s)(?:-p|--password=)(\"[^\"]*\"|'[^']*'|[^\s\"']+)")


def _command(match: re.Match, text: str) -> dict[str, Any] | None:
    """One psql or mysql command's connection details, with the password it carries if any."""
    tool, args = match.group(1), match.group(2)
    host = re.search(r"(?:-h\s*|--host[= ])([A-Za-z0-9.-]+)", args)
    if not host or is_placeholder(host.group(1)):
        return None
    mysql = tool.startswith("my")
    port = re.search(r"(?:-p\s*|--port[= ])(\d{2,5})" if not mysql else
                     r"(?:-P\s*|--port[= ])(\d{2,5})", args)
    user = re.search(r"(?:-U\s*|--username[= ])([\w.@-]+)" if not mysql else
                     r"(?:-u\s*|--user[= ])([\w.@-]+)", args)
    named = re.search(r"(?:-d\s*|--dbname[= ])([\w.-]+)" if not mysql else
                      r"(?:-D\s*|--database[= ])([\w.-]+)", args)
    database = named.group(1) if named else None
    words = args.split()
    if mysql and not database and len(words) > 1 and re.fullmatch(r"[\w.-]+", words[-1]) \
            and words[-2] not in ("-h", "-P", "-u", "-D", "-S", "--host", "--port", "--user"):
        # mysql takes the database as its last word.
        database = words[-1]
    line_start = text.rfind("\n", 0, match.start()) + 1
    before = PASSWORD_ENV.search(text, line_start, match.start())
    given = MYSQL_PASSWORD.search(args) if mysql else None
    password = (before or given).group(1).strip("\"'") if before or given else None
    if password and PLACEHOLDER.search(password):
        password = None
    return {"engine": "mysql" if mysql else "postgresql", "host": host.group(1),
            "port": int(port.group(1)) if port else None, "database": database,
            "user": user.group(1) if user else None, "has_password": bool(password),
            "password": password}


def find_commands(text: str) -> Iterator[dict[str, Any]]:
    for match in COMMAND.finditer(text):
        yield from find_urls(match.group(2))
        found = _command(match, text)
        if found:
            found.pop("password")
            yield found


SUPABASE_API = re.compile(r"^([a-z0-9]{20})\.supabase\.co$")


def database_host(host: str) -> str:
    """The database a provider host stands for: a Supabase project's API host names the
    project, whose database is db.<ref>.supabase.co."""
    api = SUPABASE_API.match(host.lower())
    return f"db.{api.group(1)}.supabase.co" if api else host


def find_hosts(text: str) -> Iterator[str]:
    """Managed database host names named on their own, like a PGHOST value."""
    for match in HOST_TEXT.finditer(text):
        host = database_host(match.group(0).lower())
        if not is_placeholder(host):
            yield host


def find_cloud_sql(text: str) -> Iterator[str]:
    for match in CLOUD_SQL.finditer(text):
        yield match.group(1)


def find_all(text: str) -> list[dict[str, Any]]:
    """Every database ``text`` names, one per server and database."""
    found: dict[tuple, dict[str, Any]] = {}
    lower = text.lower()
    # Each kind of trace has a cheap tell; most files have none, and skip the patterns.
    sources = [("url", find_urls(text))] if "://" in text else []
    if "host=" in lower or "server=" in lower or "data source=" in lower:
        sources.append(("settings", find_keyvalue(text)))
    if any(tool in lower for tool in ("psql", "mysql", "pg_dump", "pgcli", "mycli")):
        sources.append(("command", find_commands(text)))
    for via, items in sources:
        for item in items:
            item["via"] = via
            key = (item["host"].lower(), item["port"], item["database"])
            if key not in found or item["has_password"] and not found[key]["has_password"]:
                found[key] = item
    named = {key[0] for key in found}
    hosts = find_hosts(text) if any(tell in lower for tell in HOST_TELLS) else ()
    for host in hosts:
        if host not in named:
            named.add(host)
            mysql = "mysql" in host or "psdb" in host or "tidb" in host
            found[(host, None, None)] = {"engine": "mysql" if mysql else "postgresql",
                                         "host": host, "port": None, "database": None,
                                         "user": None, "has_password": False, "via": "host"}
    instances = find_cloud_sql(text) if "cloud" in lower or "instance" in lower else ()
    for instance in instances:
        found[(instance, None, None)] = {"engine": "postgresql", "host": None, "port": None,
                                         "database": None, "user": None,
                                         "has_password": False, "provider": "gcp",
                                         "instance": instance, "via": "cloudsql"}
    return list(found.values())


# Where a project keeps its secrets when they aren't in a file RDST can read: a marker
# file, or a command or reference in its config and scripts.
SECRET_FILES = {".infisical.json": "Infisical", "doppler.yaml": "Doppler",
                ".doppler.yaml": "Doppler", ".sops.yaml": "SOPS", ".env.vault": "dotenv-vault",
                ".env.keys": "dotenvx", ".chamber": "Chamber", "secrets.tfvars": "Terraform"}
SECRET_TEXT = (
    (re.compile(r"\binfisical\s+(run|export|secrets)\b|@infisical/|infisical_client|"
                r"INFISICAL_(TOKEN|PROJECT_ID|CLIENT_ID)"), "Infisical"),
    (re.compile(r"\bdoppler\s+(run|secrets)\b|DOPPLER_TOKEN"), "Doppler"),
    (re.compile(r"\bop://[^\s\"']+|\bop\s+(run|read|inject)\b"), "1Password"),
    (re.compile(r"\bVAULT_ADDR\b|\bvault\s+kv\s+get\b|hashicorp/vault|hvac\.Client"), "Vault"),
    (re.compile(r"\bsops\s+(-d|--decrypt|exec-env)\b"), "SOPS"),
    (re.compile(r"\bdotenvx\s+run\b|\bnpx\s+dotenv-vault\b"), "dotenvx"),
    (re.compile(r"secretsmanager|SecretsManager|aws-secretsmanager|"
                r"aws_secretsmanager_secret|GetSecretValue"), "AWS Secrets Manager"),
    (re.compile(r"\bssm:/|aws_ssm_parameter|GetParameter\(|ssm\.get_parameter"),
     "AWS Parameter Store"),
    (re.compile(r"projects/[\w-]+/secrets/|secretmanager\.googleapis|"
                r"google-cloud-secret-manager|@google-cloud/secret-manager|"
                r"SecretManagerServiceClient"), "Google Secret Manager"),
    (re.compile(r"@Microsoft\.KeyVault\(|\.vault\.azure\.net|azure-keyvault|"
                r"@azure/keyvault-secrets|SecretClient\("), "Azure Key Vault"),
    (re.compile(r"\bbws\s+secret\b|@bitwarden/sdk"), "Bitwarden Secrets Manager"),
    (re.compile(r"\bakeyless\b"), "Akeyless"),
    (re.compile(r"\besc\s+(run|env\s+open)\b|pulumi\s+env\s+run"), "Pulumi ESC"),
    (re.compile(r"ExternalSecret|external-secrets\.io"), "External Secrets"),
)


SECRET_TELLS = ("infisical", "doppler", "op://", "op run", "op read", "op inject", "vault",
                "sops", "dotenv", "secretsmanager", "ssm", "/secrets/", "keyvault",
                "bws secret", "bitwarden", "akeyless", "esc ", "externalsecret",
                "getparameter", "secretclient", "secret-manager", "secret_manager")


def secret_managers(name: str, text: str | None) -> set[str]:
    found = {SECRET_FILES[name]} if name in SECRET_FILES else set()
    if text and any(tell in text.lower() for tell in SECRET_TELLS):
        found |= {label for pattern, label in SECRET_TEXT if pattern.search(text)}
    return found


# A dependency that means the project talks to a database, by manifest. The engine is
# None for drivers and ORMs that work with either.
DRIVERS: tuple[tuple[re.Pattern[str], str | None], ...] = tuple(
    (re.compile(pattern), engine) for pattern, engine in (
        (r"[\"'](pg|postgres|pg-promise|@neondatabase/serverless|@vercel/postgres|"
         r"postgresql|asyncpg|psycopg2?(-binary)?|psycopg\[.*?\]|psycopg|pgx|lib/pq|"
         r"tokio-postgres|sqlx-postgres|org\.postgresql)[\"'@:=\s]", "postgresql"),
        (r"\b(psycopg2?(-binary)?|psycopg|asyncpg|pg8000)\b", "postgresql"),
        (r"github\.com/(jackc/pgx|lib/pq)", "postgresql"),
        (r"\bgem\s+[\"']pg[\"']", "postgresql"),
        (r"org\.postgresql|<artifactId>postgresql</artifactId>|Npgsql", "postgresql"),
        (r"\bpostgrex\b|\bpostgres\s*=", "postgresql"),
        (r"[\"'](mysql2?|mariadb|@planetscale/database)[\"'@:\s]", "mysql"),
        (r"\b(mysqlclient|pymysql|mysql-connector-python|aiomysql)\b", "mysql"),
        (r"github\.com/go-sql-driver/mysql|\bgem\s+[\"']mysql2[\"']|"
         r"mysql-connector-j|MySqlConnector|MySql\.Data|\bmyxql\b", "mysql"),
        (r"[\"'](@supabase/supabase-js|supabase)[\"'@:=\s]|\bsupabase\s*=", "postgresql"),
        (r"[\"'](prisma|@prisma/client|drizzle-orm|typeorm|sequelize|knex|kysely|"
         r"@mikro-orm/core|objection)[\"'@:\s]", None),
        (r"\b(sqlalchemy|SQLAlchemy|django|Django|peewee|tortoise-orm|sqlmodel|"
         r"databases)\b", None),
        (r"gorm\.io|entgo\.io|github\.com/jmoiron/sqlx|\bdiesel\b|\bsqlx\b|\bsea-orm\b|"
         r"\bactiverecord\b|\brails\b|\becto_sql\b|laravel/framework|doctrine/orm|"
         r"EntityFrameworkCore|hibernate|spring-boot-starter-data-jpa|spring-boot-starter-jdbc",
         None)))
MANIFESTS = {"package.json", "requirements.txt", "pyproject.toml", "pipfile", "gemfile",
             "go.mod", "cargo.toml", "composer.json", "pom.xml", "build.gradle",
             "build.gradle.kts", "mix.exs", "setup.py", "setup.cfg", "poetry.lock"}


def drivers(name: str, text: str) -> tuple[bool, set[str]]:
    """Whether a manifest pulls in a database library, and which engines it names."""
    uses, engines = False, set()
    if name.lower() not in MANIFESTS and not name.lower().endswith(".csproj") and \
            not name.lower().startswith("requirements"):
        return False, set()
    for pattern, engine in DRIVERS:
        if pattern.search(text):
            uses = True
            if engine:
                engines.add(engine)
    return uses, engines


# Infrastructure that declares a managed database: Terraform, Render, Heroku and Fly.
INFRA = (
    (re.compile(r'resource\s+"aws_(db_instance|rds_cluster)"\s+"([\w-]+)"'), "aws"),
    (re.compile(r'resource\s+"google_(sql_database_instance|alloydb_cluster)"\s+"([\w-]+)"'),
     "gcp"),
    (re.compile(r'resource\s+"azurerm_(postgresql|mysql)_(flexible_)?server"\s+"([\w-]+)"'),
     "azure"),
    (re.compile(r'resource\s+"digitalocean_database_cluster"\s+"([\w-]+)"'), "digitalocean"),
    (re.compile(r'resource\s+"(supabase_project|neon_project)"\s+"([\w-]+)"'), None),
    (re.compile(r"AWS::RDS::DB(Instance|Cluster)"), "aws"),
    (re.compile(r"rds\.DatabaseInstance|rds\.DatabaseCluster|aws_rds\.Database"), "aws"),
    (re.compile(r"heroku-postgresql|cleardb|jawsdb"), "heroku"),
    (re.compile(r"^databases:\s*$", re.MULTILINE), "render"),
    (re.compile(r"fly\s+postgres\s+attach|flyio/postgres"), "fly"),
)


def infrastructure(name: str, text: str) -> list[dict[str, Any]]:
    """The managed databases infrastructure files declare, by provider and engine."""
    lower = name.lower()
    if not (lower.endswith((".tf", ".tf.json", ".ts", ".py", ".json", ".yaml", ".yml",
                            ".toml"))):
        return []
    if lower == "render.yaml" or lower == "app.json" or lower.endswith(".tf") or \
            lower.endswith(".tf.json") or "cdk" in lower or "stack" in lower or \
            lower in ("fly.toml", "template.yaml", "template.yml", "serverless.yml"):
        found = []
        for pattern, provider in INFRA:
            if provider == "render" and lower != "render.yaml":
                continue
            for match in pattern.finditer(text):
                label = match.group(match.lastindex) if match.lastindex else None
                mysql = "mysql" in match.group(0).lower() or "cleardb" in match.group(0) \
                    or "jawsdb" in match.group(0)
                found.append({"provider": provider or (
                    "supabase" if "supabase" in match.group(0) else "neon"),
                    "engine": "mysql" if mysql else "postgresql",
                    "label": label})
        return found
    return []


SNIPPET_TELLS = ("://", "psql ", "pg_dump ", "mysql ", "mysqldump ", "Host=", "Server=",
                 "INSTANCE_CONNECTION_NAME", "cloudsql", *HOST_TELLS)
SNIPPET_SPAN = 400


def snippets(text: str) -> str:
    """Just the parts of a long log around something that can name a database, joined, so
    the patterns read kilobytes instead of megabytes."""
    spans: list[tuple[int, int]] = []
    for tell in SNIPPET_TELLS:
        index = text.find(tell)
        while index != -1:
            spans.append((max(0, index - SNIPPET_SPAN), index + SNIPPET_SPAN))
            index = text.find(tell, index + SNIPPET_SPAN)
    if not spans:
        return ""
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return "\n".join(text[start:end] for start, end in merged)


# Walking a project. This module runs inside WSL too (see main), so it uses only the
# standard library.
WALK_DEPTH = 4
WALK_ENTRY_LIMIT = 8000
PROJECT_READ_BYTES = 24 << 20
CONFIG_READ_LIMIT = 256 * 1024
SOURCE_READ_LIMIT = 200 * 1024
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "env", "__pycache__", "dist", "build",
             "target", ".next", ".nuxt", ".cache", "vendor", "coverage", ".terraform",
             ".idea", ".vscode", "data", "logs", "tmp", ".pytest_cache", ".mypy_cache",
             "site-packages", ".gradle", "bin", "obj", "Pods", ".svelte-kit", ".turbo",
             ".yarn", "bower_components", "public", "static", "assets", "fixtures", "test",
             "tests", "__tests__", "spec", "e2e", "examples", "example", "docs", "samples",
             "testdata", "__fixtures__", "__mocks__", "node", ".angular", ".expo", "out",
             "python_modules", "third_party", "_vendor", "deps", "benchmarks", "bench",
             "mocks", "stubs", "seeds", "demo", "demos"}
EXAMPLE = re.compile(r"\.(example|sample|template|dist|defaults?)$|example", re.IGNORECASE)
CONFIG_SUFFIXES = (".yaml", ".yml", ".json", ".toml", ".ini", ".properties", ".xml", ".conf",
                   ".cfg", ".tf", ".tfvars", ".hcl", ".prisma", ".exs", ".config", ".neon")
SOURCE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".mts", ".go", ".rb", ".php",
                   ".java", ".kt", ".kts", ".cs", ".rs", ".ex", ".scala", ".sh", ".ps1")
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock",
              "cargo.lock", "poetry.lock", "gemfile.lock", "go.sum", "tsconfig.json",
              "jsconfig.json", ".eslintrc.json", "biome.json", ".prettierrc.json",
              "angular.json", "turbo.json", "renovate.json", ".babelrc", "deno.lock"}
TEST_FILE = re.compile(r"(^test_|_test\.|\.test\.|\.spec\.|\.min\.|\.d\.ts$|conftest\.py$)",
                       re.IGNORECASE)
# Read config before code, so the budget goes to what names databases most often.
KIND_ORDER = {"dotenv": 0, "compose": 1, "database_yml": 1, "secret": 1, "manifest": 2,
              "config": 3, "source": 4}
# Files RDST parses whole; the rest it reads for traces.
STRUCTURED = ("dotenv", "compose", "database_yml")


def kind(name: str, folder: str) -> str | None:
    """What a project file can say about its database, by its name."""
    import os

    lower = name.lower()
    if lower == "datasources.xml" and os.path.basename(folder) == ".idea":
        return "config"
    if lower == ".env" or lower.startswith(".env.") or lower.endswith(".env"):
        return None if lower in (".env.vault", ".env.keys") else "dotenv"
    # The main Compose files and the ones for running locally; variants for benchmarks or
    # CI describe databases the project doesn't use day to day.
    if re.fullmatch(r"(docker-)?compose(\.(override|dev|development|local))?\.ya?ml", lower):
        return "compose"
    if re.fullmatch(r"(docker-)?compose[.-].*\.ya?ml", lower):
        return None
    if lower == "database.yml" and os.path.basename(folder) == "config":
        return "database_yml"
    if lower in SECRET_FILES:
        return "secret"
    if lower in MANIFESTS or lower.endswith(".csproj") or \
            re.fullmatch(r"requirements.*\.txt", lower):
        return "manifest"
    if lower in SKIP_FILES or TEST_FILE.search(lower):
        return None
    if lower.endswith(CONFIG_SUFFIXES) or lower in ("dockerfile", "procfile"):
        return "config"
    if lower.endswith(SOURCE_SUFFIXES):
        return "source"
    return None


def project_files(folder: str) -> list[str]:
    """The files in ``folder`` that can name a database, down to WALK_DEPTH levels."""
    import os

    out: list[str] = []
    seen = 0
    pending = [(folder, 0)]
    while pending and seen < WALK_ENTRY_LIMIT:
        current, depth = pending.pop(0)
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            name = entry.name
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                # JetBrains IDEs keep a project's saved database connections here.
                if name == ".idea" and os.path.isfile(os.path.join(entry.path,
                                                                   "dataSources.xml")):
                    out.append(os.path.join(entry.path, "dataSources.xml"))
                if depth < WALK_DEPTH and name not in SKIP_DIRS:
                    pending.append((entry.path, depth + 1))
            elif kind(name, current) and not EXAMPLE.search(name):
                out.append(entry.path)
    return out


def read(path: str, limit: int) -> str | None:
    import os

    try:
        if os.path.getsize(path) > limit:
            return None
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def tail(path: str, limit: int) -> str:
    """The last ``limit`` bytes of a log, where its latest work is."""
    import os

    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def read_project(folder: str, paths: list[str] | None = None) -> dict[str, Any]:
    """What a project's unstructured files say: each file's traces, whether a manifest pulls
    in a database library, its secret managers and declared infrastructure. Files RDST parses
    whole (.env, Compose, database.yml) come back by path."""
    import os

    paths = project_files(folder) if paths is None else paths
    kinds = {path: kind(os.path.basename(path), os.path.dirname(path)) for path in paths}
    result: dict[str, Any] = {"folder": folder, "structured": [], "files": [], "uses": None,
                              "engines": [], "managers": [], "infra": []}
    engines: set[str] = set()
    managers: set[str] = set()
    budget = PROJECT_READ_BYTES
    for path in sorted((p for p in paths if kinds[p]), key=lambda p: KIND_ORDER[kinds[p]]):
        file_kind, name = kinds[path], os.path.basename(path)
        rel = os.path.relpath(path, folder)
        if file_kind in STRUCTURED:
            result["structured"].append(path)
            continue
        if file_kind == "secret":
            managers |= secret_managers(name.lower(), None)
            continue
        text = read(path, SOURCE_READ_LIMIT if file_kind == "source" else CONFIG_READ_LIMIT)
        if text is None:
            continue
        budget -= len(text)
        if budget < 0:
            break
        if file_kind == "manifest":
            used, named = drivers(name, text)
            if used and result["uses"] is None:
                result["uses"] = rel
            engines |= named
        if file_kind != "source":
            managers |= secret_managers(name.lower(), text)
        result["infra"] += [{**entry, "rel": rel} for entry in infrastructure(name, text)]
        found = find_all(text)
        if found:
            result["files"].append({"path": path, "rel": rel, "kind": file_kind,
                                    "traces": found})
    result["engines"], result["managers"] = sorted(engines), sorted(managers)
    return result


def recent_sessions(home: str, days: int, per_project: int, codex_limit: int = 120
                    ) -> list[tuple[str, str]]:
    """The newest agent sessions under ``home``, a few per Claude Code project and the
    newest Codex ones: each file and whose it is."""
    import glob
    import os
    import time

    since = time.time() - days * 86400
    picked: list[tuple[str, str]] = []
    for folder in glob.glob(os.path.join(home, ".claude", "projects", "*")):
        try:
            files = sorted(((os.path.getmtime(path), path)
                            for path in glob.glob(os.path.join(folder, "*.jsonl"))),
                           reverse=True)
        except OSError:
            continue
        picked += [(path, "Claude Code") for mtime, path in files[:per_project]
                   if mtime >= since]
    codex = []
    for path in glob.glob(os.path.join(home, ".codex", "sessions", "*", "*", "*", "*.jsonl")):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime >= since:
            codex.append((mtime, path))
    picked += [(path, "Codex") for _, path in sorted(codex, reverse=True)[:codex_limit]]
    return picked


def session_folder(path: str) -> str | None:
    """The folder a session ran in, from its first lines."""
    import json

    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for _, line in zip(range(40), handle):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                payload = event.get("payload")
                cwd = event.get("cwd") or (payload.get("cwd") if isinstance(payload, dict)
                                           else None)
                if isinstance(cwd, str) and cwd:
                    return cwd
    except OSError:
        return None
    return None


SHELL_HISTORIES = (".bash_history", ".zsh_history", ".local/share/fish/fish_history")


def main(argv: list[str]) -> None:
    """Inside WSL, for RDST on Windows: read projects or history here, where it's fast, and
    print one JSON line per project, session or history file."""
    import json
    import os

    request = json.loads(argv[1])
    if request["mode"] == "projects":
        for folder in request["folders"]:
            print(json.dumps(read_project(folder)), flush=True)
        return
    home = os.path.expanduser("~")
    per_folder: dict[str | None, int] = {}
    for path, agent in recent_sessions(home, request["days"], request["per_project"]):
        folder = session_folder(path)
        per_folder[folder] = per_folder.get(folder, 0) + 1
        if agent == "Codex" and per_folder[folder] > request["per_project"]:
            continue
        found = find_all(snippets(tail(path, request["session_bytes"])))
        if found:
            print(json.dumps({"folder": folder, "agent": agent, "traces": found}), flush=True)
    for name in SHELL_HISTORIES:
        path = os.path.join(home, name)
        found = find_all(snippets(tail(path, request["history_bytes"])))
        if found:
            print(json.dumps({"folder": None, "agent": "shell", "traces": found}), flush=True)


if __name__ == "__main__":
    import sys

    main(sys.argv)
