"""§8 Replication Health — peers, lag, slots, WAL generation.

PostgreSQL: pg_stat_replication (per peer, from primary's perspective),
pg_replication_slots (logical + physical), pg_stat_wal_receiver (when running
on a standby). WAL generation is estimated from pg_current_wal_lsn() delta
over a 1-second sleep — kept short to avoid blocking the audit.

MySQL: SHOW REPLICA STATUS / SHOW BINARY LOG STATUS.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from features.audit.health.models import (
    ReplicationHealthSection,
    ReplicationPeer,
    ReplicationSlot,
)


def collect_replication_health(
    conn: Any, engine: str
) -> Optional[ReplicationHealthSection]:
    engine = (engine or "").lower()
    if engine == "postgresql":
        return _collect_pg(conn)
    if engine == "mysql":
        return _collect_mysql(conn)
    return None


def _collect_pg(conn: Any) -> ReplicationHealthSection:
    section = ReplicationHealthSection()

    with conn.cursor() as cur:
        try:
            cur.execute("SELECT pg_is_in_recovery();")
            row = cur.fetchone()
            section.is_replica = bool(row and row[0])
        except Exception:
            pass

        # If this is a standby, pull upstream status
        if section.is_replica:
            try:
                cur.execute(
                    """
                    SELECT status, sender_host,
                           EXTRACT(EPOCH FROM (now() - last_msg_receipt_time)) AS lag_sec
                    FROM pg_stat_wal_receiver
                    LIMIT 1;
                    """
                )
                row = cur.fetchone()
                if row:
                    section.wal_receiver_status = row[0]
                    section.primary_conninfo_host = row[1]
                    if row[2] is not None:
                        section.upstream_lag_seconds = float(row[2])
            except Exception:
                pass

            try:
                cur.execute(
                    """
                    SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()));
                    """
                )
                row = cur.fetchone()
                if row and row[0] is not None:
                    section.upstream_lag_seconds = float(row[0])
            except Exception:
                pass

        # Per-peer rows (from primary's perspective)
        try:
            cur.execute(
                """
                SELECT
                    application_name,
                    client_addr::text,
                    state,
                    sync_state,
                    sent_lsn::text,
                    write_lsn::text,
                    flush_lsn::text,
                    replay_lsn::text,
                    EXTRACT(EPOCH FROM write_lag) * 1000,
                    EXTRACT(EPOCH FROM flush_lag) * 1000,
                    EXTRACT(EPOCH FROM replay_lag) * 1000
                FROM pg_stat_replication;
                """
            )
            for row in cur.fetchall():
                section.peers.append(
                    ReplicationPeer(
                        application_name=row[0],
                        client_addr=row[1],
                        state=row[2] or "",
                        sync_state=row[3],
                        sent_lsn=row[4],
                        write_lsn=row[5],
                        flush_lsn=row[6],
                        replay_lsn=row[7],
                        write_lag_ms=float(row[8]) if row[8] is not None else None,
                        flush_lag_ms=float(row[9]) if row[9] is not None else None,
                        replay_lag_ms=float(row[10]) if row[10] is not None else None,
                    )
                )
        except Exception:
            pass

        # Replication slots
        try:
            cur.execute(
                """
                SELECT
                    slot_name,
                    slot_type,
                    active,
                    restart_lsn::text,
                    confirmed_flush_lsn::text,
                    CASE
                        WHEN restart_lsn IS NOT NULL
                        THEN pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)
                        ELSE NULL
                    END AS retained_bytes
                FROM pg_replication_slots;
                """
            )
            for row in cur.fetchall():
                section.slots.append(
                    ReplicationSlot(
                        slot_name=row[0],
                        slot_type=row[1],
                        active=bool(row[2]),
                        restart_lsn=row[3],
                        confirmed_flush_lsn=row[4],
                        retained_bytes=int(row[5]) if row[5] is not None else None,
                    )
                )
            section.inactive_slots = sum(1 for s in section.slots if not s.active)
        except Exception:
            pass

        # WAL generation rate — quick 1s sample, skipped on standby
        if not section.is_replica:
            try:
                cur.execute("SELECT pg_current_wal_lsn();")
                lsn_a = cur.fetchone()[0]
                time.sleep(1)
                cur.execute(
                    "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), %s);", (lsn_a,)
                )
                bytes_per_sec = float(cur.fetchone()[0] or 0)
                section.wal_generation_mb_per_min = round(
                    (bytes_per_sec * 60) / (1024 * 1024), 2
                )
            except Exception:
                pass

    return section


def _collect_mysql(conn: Any) -> ReplicationHealthSection:
    section = ReplicationHealthSection()
    try:
        with conn.cursor() as cur:
            # Try MySQL 8.0.22+ syntax first, fall back to SHOW SLAVE STATUS
            for stmt in ("SHOW REPLICA STATUS", "SHOW SLAVE STATUS"):
                try:
                    cur.execute(stmt)
                    break
                except Exception:
                    continue
            else:
                return section

            cols = [d[0] for d in cur.description] if cur.description else []
            row = cur.fetchone()
            if row:
                data = dict(zip(cols, row))
                section.is_replica = True
                lag = data.get("Seconds_Behind_Source") or data.get(
                    "Seconds_Behind_Master"
                )
                if lag is not None:
                    try:
                        section.upstream_lag_seconds = float(lag)
                    except (TypeError, ValueError):
                        pass
                section.wal_receiver_status = (
                    "streaming"
                    if data.get("Replica_IO_Running") in ("Yes", b"Yes")
                    or data.get("Slave_IO_Running") in ("Yes", b"Yes")
                    else "stopped"
                )
    except Exception:
        pass
    return section
