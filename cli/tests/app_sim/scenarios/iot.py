"""iot: a write-heavy sensor ingest service with a small dashboard.

Gateways post batches of 50 readings (one device, 5 metrics x 10 samples) as one multi-row
INSERT, then bump the device's last_seen_at. Device activity is skewed, so a few device rows are
updated constantly. Dashboards read short time ranges; a retention job deletes readings older
than RETENTION every 15 seconds, so the table stays near RETENTION x ingest rate rows. Time is
compressed: "retention" is 30 minutes so the database stays small.

Seed timestamps are relative to setup time (live_anchor), so dashboards see data immediately.

Plants:
  I1-I4 readings carries 7 secondary indexes. readings_value_idx, readings_quality_idx,
        readings_batch_idx and readings_received_at_idx are never read by any query, and every
        ingested row maintains them. Drop all four.
  I5    readings_device_time_idx, readings_site_time_idx and readings_recorded_at_idx serve the
        device chart, the site overview and the retention DELETE. Keep them.
  I6    No new index belongs on readings: the dashboards are already served and every extra
        index taxes ~200 inserts/s. devices.last_seen_at is hot-updated and only read by a rare
        stale-device sweep over 2000 rows; indexing it would also end HOT updates on
        PostgreSQL.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Iterator

from tests.app_sim.scenarios.base import BOTH, Scenario, skewed

METRICS = ("temp_c", "humidity", "pressure_hpa", "co2_ppm", "vibration")
MODELS = ("TH-100", "TH-200", "AQ-5", "VB-9")
REGIONS = ("eu-west", "us-east", "us-west", "ap-south")
BATCH_ROWS = 50
SAMPLES = BATCH_ROWS // len(METRICS)
RETENTION = timedelta(minutes=30)
READINGS_COLUMNS = ("device_id", "site_id", "metric", "value", "quality", "batch_id",
                    "recorded_at", "received_at")


def device_site(device: int, n_sites: int) -> int:
    return 1 + (device * 7) % n_sites


def device_serial(device: int) -> str:
    return f"DEV-{device:06d}"


def sample(rng: random.Random, metric: str) -> float:
    base = {"temp_c": 21.0, "humidity": 45.0, "pressure_hpa": 1013.0, "co2_ppm": 600.0,
            "vibration": 0.2}[metric]
    return round(base * (0.8 + 0.4 * rng.random()), 3)


class App(Scenario):
    name = "iot"
    live_anchor = True
    tables = {
        "sites": ("id {pk}", "name varchar(80) NOT NULL", "region varchar(16) NOT NULL"),
        "devices": ("id {pk}", "site_id bigint NOT NULL", "serial varchar(24) NOT NULL",
                    "model varchar(16) NOT NULL", "firmware varchar(16) NOT NULL",
                    "status varchar(16) NOT NULL", "battery_pct smallint NOT NULL",
                    "last_seen_at {ts} NOT NULL"),
        "readings": ("id {pk}", "device_id bigint NOT NULL", "site_id bigint NOT NULL",
                     "metric varchar(16) NOT NULL", "value {float} NOT NULL",
                     "quality smallint NOT NULL", "batch_id bigint NOT NULL",
                     "recorded_at {ts} NOT NULL", "received_at {ts} NOT NULL"),
    }
    indexes = (
        ("devices_serial_key", "devices", ("serial",), True, None),
        ("devices_site_idx", "devices", ("site_id",), False, None),
        ("readings_device_time_idx", "readings", ("device_id", "recorded_at"), False, "I5"),
        ("readings_site_time_idx", "readings", ("site_id", "recorded_at"), False, "I5"),
        ("readings_recorded_at_idx", "readings", ("recorded_at",), False, "I5"),
        ("readings_value_idx", "readings", ("value",), False, "I1"),
        ("readings_quality_idx", "readings", ("quality",), False, "I2"),
        ("readings_batch_idx", "readings", ("batch_id",), False, "I3"),
        ("readings_received_at_idx", "readings", ("received_at",), False, "I4"),
    )
    base_sizes = {"sites": 40, "devices": 2000, "readings": 360000}
    queries = {
        "device_by_serial": ("SELECT devices.id, devices.site_id, devices.status FROM devices "
                             "WHERE devices.serial = %(serial_1)s"),
        "readings_insert": ("INSERT INTO readings (" + ", ".join(READINGS_COLUMNS) + ") "
                            "VALUES {values}"),
        # Hot rows: skewed devices report constantly.
        "device_touch": ("UPDATE devices SET last_seen_at=%(last_seen_at)s, "
                         "battery_pct=%(battery_pct)s WHERE devices.id = %(id_1)s"),
        "device_chart": ("SELECT readings.recorded_at, readings.value FROM readings "
                         "WHERE readings.device_id = %(device_id_1)s AND readings.metric = "
                         "%(metric_1)s AND readings.recorded_at >= %(recorded_at_1)s "
                         "ORDER BY readings.recorded_at"),
        "site_devices": ("SELECT devices.id, devices.serial, devices.model, devices.status, "
                         "devices.battery_pct, devices.last_seen_at FROM devices "
                         "WHERE devices.site_id = %(site_id_1)s ORDER BY devices.serial"),
        "site_overview": ("SELECT readings.metric, avg(readings.value) AS avg_1, "
                          "max(readings.value) AS max_1, count(*) AS count_1 FROM readings "
                          "WHERE readings.site_id = %(site_id_1)s AND readings.recorded_at >= "
                          "%(recorded_at_1)s GROUP BY readings.metric"),
        "stale_devices": ("SELECT devices.id, devices.serial, devices.last_seen_at FROM devices "
                          "WHERE devices.last_seen_at < %(last_seen_at_1)s "
                          "ORDER BY devices.last_seen_at LIMIT %(param_1)s"),
        "readings_retention": ("DELETE FROM readings WHERE readings.recorded_at < "
                               "%(recorded_at_1)s"),
        # A function on the indexed column hides it from the planner; comparing the bare
        # column to the minute is the same filter and can use readings_recorded_at_idx.
        "metric_by_site": ("SELECT readings.site_id, avg(readings.value) AS avg_1 FROM readings "
                           "WHERE date_trunc('minute', readings.recorded_at) >= %(since_1)s "
                           "AND readings.metric = %(metric_1)s GROUP BY readings.site_id"),
    }
    journeys = {"ingest": 70, "device_chart": 20, "site_overview": 10, "metric_by_site": 6}
    jobs = (("retention", 15.0), ("stale_sweep", 30.0))
    default_rps = 6.0
    default_threads = 3

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.next_batch = 10 ** 7 + random.Random(f"iot:batch:{self.engine}").randrange(10 ** 6)

    def rows(self, table: str, seed: int = 7) -> Iterator[tuple[Any, ...]]:
        rng = random.Random(f"iot:{table}:{seed}")
        n, anchor = self.n, self.anchor
        if table == "sites":
            for i in range(1, n[table] + 1):
                yield (i, f"Plant {i}", REGIONS[i % len(REGIONS)])
        elif table == "devices":
            for i in range(1, n[table] + 1):
                yield (i, device_site(i, n["sites"]), device_serial(i), MODELS[i % len(MODELS)],
                       f"1.{rng.randrange(9)}.{rng.randrange(20)}",
                       "active" if rng.random() < 0.97 else "maintenance",
                       rng.randrange(5, 101), anchor - timedelta(seconds=rng.randrange(3600)))
        elif table == "readings":
            batches = n[table] // BATCH_ROWS
            step = RETENTION.total_seconds() / max(1, batches)
            row_id = 0
            for b in range(batches):
                at = anchor - RETENTION + timedelta(seconds=b * step)
                device = skewed(rng, n["devices"], 1.6)
                for metric in METRICS:
                    for s in range(SAMPLES):
                        row_id += 1
                        recorded = at - timedelta(seconds=SAMPLES - s)
                        yield (row_id, device, device_site(device, n["sites"]), metric,
                               sample(rng, metric), 1 if rng.random() < 0.98 else 0, b + 1,
                               recorded, at)
        else:
            raise KeyError(table)

    # Traffic

    def j_ingest(self, db: Any, rng: random.Random) -> None:
        device = skewed(rng, self.n["devices"], 1.6)
        row = db.one("device_by_serial", serial_1=device_serial(device))
        if not row:
            return
        site = int(row[1])
        now = datetime.now().replace(microsecond=0)
        with self.lock:
            self.next_batch += 1
            batch = self.next_batch
        values = [(device, site, metric, sample(rng, metric), 1 if rng.random() < 0.98 else 0,
                   batch, now - timedelta(seconds=SAMPLES - s), now)
                  for metric in METRICS for s in range(SAMPLES)]
        with db.transaction():
            db.listed("readings_insert", values, fetch=False)
            db.execute("device_touch", last_seen_at=now, battery_pct=rng.randrange(5, 101),
                       id_1=device)

    def j_device_chart(self, db: Any, rng: random.Random) -> None:
        device = skewed(rng, self.n["devices"], 1.6)
        since = datetime.now() - timedelta(minutes=rng.choice((5, 15, 30)))
        db.all("device_chart", device_id_1=device, metric_1=rng.choice(METRICS),
               recorded_at_1=since)

    def j_site_overview(self, db: Any, rng: random.Random) -> None:
        site = 1 + rng.randrange(self.n["sites"])
        db.all("site_devices", site_id_1=site)
        db.all("site_overview", site_id_1=site,
               recorded_at_1=datetime.now() - timedelta(minutes=rng.choice((5, 15))))

    def j_metric_by_site(self, db: Any, rng: random.Random) -> None:
        since = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=10)
        db.all("metric_by_site", since_1=since, metric_1=rng.choice(METRICS))

    def b_retention(self, db: Any, rng: random.Random) -> None:
        db.execute("readings_retention", recorded_at_1=datetime.now() - RETENTION)

    def b_stale_sweep(self, db: Any, rng: random.Random) -> None:
        db.all("stale_devices", last_seen_at_1=datetime.now() - timedelta(minutes=20),
               param_1=100)


UNUSED = ("readings_value_idx", "readings_quality_idx", "readings_batch_idx",
          "readings_received_at_idx")
USED = ("readings_device_time_idx", "readings_site_time_idx", "readings_recorded_at_idx")

EXPECTED: tuple[dict[str, Any], ...] = tuple(
    {"plant": f"I{i}", "type": "drop_index", "engines": BOTH, "table": "readings",
     "index": index, "reason": "unused", "priority": "high",
     "why": "No query reads it and readings takes ~200 inserted rows/s, each maintaining it."}
    for i, index in enumerate(UNUSED, start=1)
) + tuple(
    {"plant": "I5", "type": "keep_index", "engines": BOTH, "table": "readings", "index": index,
     "statements": statements,
     "why": "Serves a dashboard read or the retention DELETE."}
    for index, statements in zip(USED, (["device_chart"], ["site_overview"],
                                        ["readings_retention"]))
) + (
    {"plant": "I6", "type": "skip_index", "engines": BOTH, "table": "readings",
     "why": "Write-heavy table whose reads are already served; any new index is net cost."},
)

MUST_NOT: tuple[dict[str, Any], ...] = (
    {"type": "add_index", "table": "readings", "plant": "I6",
     "why": "Any new index on readings taxes every ingested row."},
    {"type": "add_index", "table": "devices", "columns": ["last_seen_at"], "plant": "I6",
     "why": "Hot-updated column on a 2000-row table read by a rare sweep."},
) + tuple({"type": "drop_index", "index": index, "plant": "I5"} for index in USED) + (
    {"type": "drop_index", "index": "devices_serial_key", "why": "Every ingest uses it."},
)
