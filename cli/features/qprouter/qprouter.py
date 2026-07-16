"""The QPRouter object: an SQP proxy plus its ReadySet backend.

Composes :class:`SqpAdminClient` and :class:`ReadysetClient` and exposes the
operations the demo needs: a unified per-pattern table joining SQP digests,
routing rules, ReadySet caches, and locally-computed QueryPilot selection
reasons, plus manual cache / uncache and QueryPilot-owned reset.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from features.qprouter.readyset_client import ReadysetClient
from features.qprouter.sqp_client import SqpAdminClient, to_decimal, to_hex

MANUAL_OWNER = "rdst-manual"
READYSET_META_PREFIX = "readyset-meta:"
VALID_DISCOVERY_MODES = {"count_star", "sum_time"}


@dataclass
class PatternRow:
    fingerprint: int                 # decimal u64 (the router key)
    fingerprint_hex: str
    sql: str
    calls: int
    sum_time_us: int
    cached: bool
    owner: str | None                # 'querypilot' | 'manual' | None
    state: str | None
    cache_name: str | None
    past_threshold: bool
    past_warmup: bool
    decision: dict[str, Any]
    # Whether ReadySet holds a cache for this fingerprint right now. Distinct
    # from `cached` (an SQP routing rule exists): rules can churn transiently
    # while the cache itself keeps serving, so data-plane consumers should key
    # off this rather than rule state.
    has_cache: bool = False
    log_reason: str | None = None
    pct_load: float = 0.0            # share of total sum_time across patterns
    alt_rank: int | None = None
    alt_metric: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # title/what/why from catalog

    def to_dict(self) -> dict[str, Any]:
        d = {
            "fingerprint": str(self.fingerprint),
            "fingerprint_hex": self.fingerprint_hex,
            "sql": self.sql,
            "calls": self.calls,
            "sum_time_us": self.sum_time_us,
            "cached": self.cached,
            "has_cache": self.has_cache,
            "owner": self.owner,
            "state": self.state,
            "cache_name": self.cache_name,
            "past_threshold": self.past_threshold,
            "past_warmup": self.past_warmup,
            "decision": self.decision,
            "reason": self.decision.get("reason"),
            "log_reason": self.log_reason,
            "pct_load": round(self.pct_load, 1),
            "alt_rank": self.alt_rank,
            "alt_metric": self.alt_metric,
        }
        d.update(self.extra)
        return d


class QPRouter:
    def __init__(self, sqp_host: str, sqp_port: int, admin_port: int,
                 api_key: str | None, readyset: ReadysetClient,
                 metrics_port: int | None = None, name: str = "qprouter"):
        self.name = name
        self.sqp_host = sqp_host
        self.sqp_port = sqp_port
        self.admin = SqpAdminClient(sqp_host, admin_port, api_key=api_key)
        self.readyset = readyset
        self.metrics_port = metrics_port

    # ------------------------------------------------------------- unified view
    def get_patterns(self, query_discovery_mode: str = "count_star",
                     number_of_queries: int = 3,
                     min_execution: int = 5,
                     denylist_path: Path | None = None,
                     log_reasons: dict[int, str] | None = None) -> list[PatternRow]:
        if query_discovery_mode not in VALID_DISCOVERY_MODES:
            raise ValueError(f"unsupported query_discovery_mode: {query_discovery_mode}")
        digests = self.admin.digests(limit=500, order_by=query_discovery_mode)
        rules = {to_decimal(r["fingerprint_hash"]): r for r in self.admin.pattern_rules()}
        cache_names = _cache_names_by_fingerprint(self.readyset.show_caches())
        denylisted = _denylisted_fingerprints(denylist_path)
        log_reasons = log_reasons or {}

        ranked = _rank_digests(
            digests, rules, query_discovery_mode, number_of_queries,
            min_execution, denylisted,
        )
        alt_mode = "sum_time" if query_discovery_mode == "count_star" else "count_star"
        alt_ranked = _rank_digests(
            digests, rules, alt_mode, number_of_queries,
            min_execution, denylisted,
        )
        alt_metric = _metric_name(alt_mode)

        total_time = sum(int(d.get("sum_time_us", 0)) for d in digests) or 1
        out: list[PatternRow] = []
        for d in digests:
            fp = to_decimal(d["fingerprint_hash"])
            rule = rules.get(fp)
            cached = rule is not None and rule.get("target_pool") == "readyset"
            owner = _owner(rule) if cached else None
            cache_name = _cache_name(rule) or _single(cache_names.get(fp))
            decision = _decision_for_digest(
                d, rule, owner, query_discovery_mode, number_of_queries,
                min_execution, ranked, denylisted,
            )
            out.append(PatternRow(
                fingerprint=fp,
                fingerprint_hex=to_hex(fp),
                sql=d.get("digest_text", ""),
                calls=int(d.get("count_star", 0)),
                sum_time_us=int(d.get("sum_time_us", 0)),
                cached=cached,
                has_cache=bool(cache_names.get(fp)),
                owner=owner,
                state=_rule_state(rule),
                cache_name=cache_name,
                past_threshold=int(d.get("count_star", 0)) >= min_execution,
                past_warmup=True,
                decision=decision,
                log_reason=log_reasons.get(fp),
                pct_load=100.0 * int(d.get("sum_time_us", 0)) / total_time,
                alt_rank=alt_ranked["ranks"].get(fp),
                alt_metric=alt_metric,
            ))
        out.sort(key=lambda r: r.sum_time_us, reverse=True)
        return out

    # ----------------------------------------------------------- manual caching
    def manual_cache(self, fingerprint: int | str) -> None:
        """Cache a query by hand (owner=None): CREATE CACHE on ReadySet + route at SQP."""
        fp = to_decimal(fingerprint)
        sql = self._digest_sql(fp)
        if not sql:
            raise ValueError(f"fingerprint {to_hex(fp)} not seen in live traffic yet")
        self.readyset.create_cache(sql)
        self.admin.add_manual_rule(fp, comment=_manual_comment(fp))

    def manual_uncache(self, fingerprint: int | str) -> None:
        fp = to_decimal(fingerprint)
        self.admin.drop_manual_rule(fp)
        for name in _cache_names_by_fingerprint(self.readyset.show_caches()).get(fp, []):
            self.readyset.drop_cache(name)

    # ---------------------------------------------------------- QueryPilot cron
    def reset_querypilot_caches(self, include_manual: bool = False) -> dict[str, Any]:
        """Delete QueryPilot-owned SQP rules and their ReadySet caches.

        With include_manual, also drop hand-created caches: when QueryPilot takes
        over it starts from a clean slate and owns all caching, so the manual
        picks the visitor made first are cleared."""
        rules = self.admin.pattern_rules()
        digests = {to_decimal(d["fingerprint_hash"]): d for d in self.admin.digests(limit=500)}
        cache_names = _cache_names_by_fingerprint(self.readyset.show_caches())
        dropped_rules = 0
        dropped_caches: list[str] = []
        errors: list[str] = []
        for rule in rules:
            fp = to_decimal(rule["fingerprint_hash"])
            if rule.get("target_pool") != "readyset":
                continue
            if _owner(rule) == "manual" and not include_manual:
                continue
            names = set(cache_names.get(fp, []))
            if _cache_name(rule):
                names.add(_cache_name(rule))
            if not names and fp in digests:
                schema = digests[fp].get("schema") or self.readyset.database
                names.add(f"readyset_{schema}_d_{to_hex(fp)}")
            try:
                self.admin.drop_pattern_rule(fp)
                dropped_rules += 1
            except Exception as e:
                errors.append(f"drop rule {to_hex(fp)}: {e}")
                continue
            for name in sorted(names):
                try:
                    self.readyset.drop_cache(name)
                    dropped_caches.append(name)
                except Exception as e:
                    errors.append(f"drop cache {name}: {e}")
        if errors:
            raise RuntimeError("; ".join(errors))
        return {"dropped_rules": dropped_rules, "dropped_caches": dropped_caches}

    # ------------------------------------------------------------------ helpers
    def _digest_sql(self, fingerprint: int) -> str | None:
        for d in self.admin.digests(limit=500):
            if to_decimal(d["fingerprint_hash"]) == fingerprint:
                return d.get("digest_text")
        return None


def _cache_name(rule: dict | None) -> str | None:
    if not rule:
        return None
    comment = rule.get("comment", "") or ""
    if READYSET_META_PREFIX in comment:
        try:
            meta = json.loads(comment.split(READYSET_META_PREFIX, 1)[1].strip())
            return meta.get("cache_name")
        except (ValueError, KeyError):
            return None
    return None


def _manual_comment(fingerprint: int) -> str:
    return json.dumps({
        "owner": MANUAL_OWNER,
        "source": "rdst-qpdemo",
        "fingerprint": to_hex(fingerprint),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, separators=(",", ":"))


def _owner(rule: dict | None) -> str | None:
    if not rule:
        return None
    comment = rule.get("comment", "") or ""
    try:
        meta = json.loads(comment)
        if meta.get("owner") == MANUAL_OWNER:
            return "manual"
    except ValueError:
        pass
    if rule.get("target_pool") == "readyset":
        return "querypilot"
    return None


def _rule_state(rule: dict | None) -> str | None:
    if not rule:
        return None
    if "enabled" in rule:
        return "active" if rule.get("enabled") else "disabled"
    return "active"


def _metric_value(digest: dict, mode: str) -> int:
    return int(digest.get("count_star" if mode == "count_star" else "sum_time_us", 0))


def _metric_name(mode: str) -> str:
    return "count_star" if mode == "count_star" else "sum_time_us"


def _is_select_shaped(sql: str) -> bool:
    sql = re.sub(r"^\s*/\*.*?\*/", "", sql or "", flags=re.S).strip().lower()
    return sql.startswith("select") or sql.startswith("with")


def _rank_digests(digests: list[dict], rules: dict[int, dict], mode: str,
                  number_of_queries: int, min_execution: int,
                  denylisted: set[int]) -> dict[str, Any]:
    eligible: list[tuple[int, int]] = []
    for d in digests:
        fp = to_decimal(d["fingerprint_hash"])
        if _owner(rules.get(fp)) == "manual":
            continue
        if fp in denylisted:
            continue
        if int(d.get("count_star", 0)) < min_execution:
            continue
        if not _is_select_shaped(d.get("digest_text", "")):
            continue
        eligible.append((fp, _metric_value(d, mode)))
    eligible.sort(key=lambda item: item[1], reverse=True)
    ranks = {fp: i + 1 for i, (fp, _) in enumerate(eligible)}
    values = {fp: value for fp, value in eligible}
    winners = eligible[:number_of_queries]
    cutoff_value = winners[-1][1] if winners else None
    return {
        "ranks": ranks,
        "values": values,
        "winner_values": [value for _, value in winners],
        "cutoff_value": cutoff_value,
    }


def _decision_for_digest(digest: dict, rule: dict | None, owner: str | None, mode: str,
                         number_of_queries: int, min_execution: int,
                         ranked: dict[str, Any], denylisted: set[int]) -> dict[str, Any]:
    fp = to_decimal(digest["fingerprint_hash"])
    metric_name = _metric_name(mode)
    metric_value = _metric_value(digest, mode)
    rank = ranked["ranks"].get(fp)
    if owner == "manual":
        return {"reason": "manual"}
    if rule is not None and rule.get("target_pool") == "readyset":
        return {
            "reason": "selected",
            "rank": rank,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "cutoff": ranked["cutoff_value"],
        }
    if fp in denylisted:
        return {"reason": "denylisted"}
    count = int(digest.get("count_star", 0))
    if count < min_execution:
        return {"reason": "below_min_execution", "count": count, "threshold": min_execution}
    if not _is_select_shaped(digest.get("digest_text", "")):
        return {"reason": "not_select_shaped"}
    return {
        "reason": "below_rank",
        "rank": rank,
        "cutoff": ranked["cutoff_value"],
        "metric_name": metric_name,
        "metric_value": metric_value,
        "winner_values": ranked["winner_values"],
    }


def _denylisted_fingerprints(path: Path | None) -> set[int]:
    if not path or not path.exists():
        return set()
    out: set[int] = set()
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        for token in line.split():
            if token.lower().startswith("digest="):
                try:
                    out.add(to_decimal(token.split("=", 1)[1]))
                except ValueError:
                    continue
    return out


def _cache_names_by_fingerprint(rows: list[tuple]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for row in rows:
        for value in row:
            if not isinstance(value, str):
                continue
            m = re.search(r"d_(0x[0-9a-fA-F]{16})", value)
            if not m:
                continue
            out.setdefault(to_decimal(m.group(1)), []).append(value)
            break
    return out


def _single(values: list[str] | None) -> str | None:
    return values[0] if values else None
