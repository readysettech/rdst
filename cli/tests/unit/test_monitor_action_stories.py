from features.monitor.action_stories import index_story, section_story

HOUR = 3600


def _plan(**fields):
    return {"add": [], "drop": [], "later": [], "skipped": [],
            "load": {"queries": 8, "queries_share": 0.89, "indexes": 12, "read_share": 0.19,
                     "write_share": 0.7,
                     "busiest_writes": [{"table": "readings", "writes_per_s": 381.0},
                                        {"table": "devices", "writes_per_s": 2.0}]},
            **fields}


def _add(table, name, share, label="safe", writes=381.0):
    return {"table": table, "label": label, "write_cost": {"writes_per_s": writes},
            "helps": [{"key": name, "name": name, "share": share}]}


def _drop(name, size=16 * 1024 * 1024):
    return {"table": "readings", "index_name": name, "table_writes_per_s": 381.0,
            "evidence_age_s": 6 * HOUR, "size_bytes": size, "confidence": "high"}


def test_the_index_story_leads_with_what_to_do_then_the_detail_behind_it():
    story = index_story(_plan(
        observed_s=5 * HOUR,
        add=[_add("readings", "AVG on readings by site_id", 0.069, label="worth_it"),
             _add("readings", "SELECT on readings by device_id", 0.071)],
        drop=[_drop("readings_value_idx"), _drop("readings_batch_idx", 10 * 1024 * 1024)],
        later=[{}]))
    assert story["headline"] == "Add 2 indexes, drop 2"
    assert story["lead"] == [
        "Add 2 indexes on readings to speed up 2 queries that take 14% of database time.",
        "Drop 2 unused indexes on readings. They slow down every write, and readings takes 381 "
        "writes a second."]
    basis, adds, drops, limit = story["details"]
    assert basis == ("Over the last 5 hours, RDST watched 8 busy queries, 89% of database time. "
                     "Reads took 19% of the time and writes 70%. Most writes hit readings, at "
                     "381 a second.")
    assert adds == ("SELECT on readings by device_id and AVG on readings by site_id filter or "
                    "sort on columns no index covers. The new indexes cover them.")
    assert drops == "No query read these indexes in the last 6 hours. Dropping them frees 26.0 MB."
    assert limit == ("The readings table takes 381 writes a second, so RDST left out 1 smaller "
                     "win. It would cost every write more than it saves.")
    assert story["facts"][:3] == [{"label": "Reads", "value": "19% of time"},
                                  {"label": "Writes", "value": "70% of time"},
                                  {"label": "Most writes", "value": "readings, 381/s"}]


def test_story_text_keeps_clear_of_banned_phrasing():
    from features.monitor.snapshot import _SLOP

    story = index_story(_plan(observed_s=5 * HOUR, add=[_add("t", "q", 0.2)],
                              drop=[{**_drop("i"), "confidence": "low"}], later=[{}]))
    for text in [story["headline"], *story["lead"], *story["details"]]:
        assert not _SLOP.search(text), text
        assert "\u2014" not in text and "\u2013" not in text


def test_an_index_story_with_nothing_to_change_says_so():
    story = index_story(_plan(load={"queries": 10, "queries_share": 0.9, "indexes": 8,
                                    "read_share": 0.77, "write_share": 0.13, "busiest_writes": []}))
    assert story["headline"] == "Your indexes fit your queries"
    assert story["lead"] == ["Your indexes match how your queries use them. None need adding "
                             "or dropping."]


def test_section_stories_agree_with_their_count():
    one = section_story("settings", [{"title": "Give PostgreSQL more memory for cached data"}])
    assert one["headline"] == "Change 1 server setting"
    assert one["lead"] == ["Give PostgreSQL more memory for cached data."]
    two = section_story("maintenance", [{"title": "Vacuum 1 bloated table"},
                                        {"title": "Refresh statistics on 2 tables"}])
    assert two["headline"] == "Run 2 maintenance tasks"
    assert two["lead"] == ["Vacuum 1 bloated table and refresh statistics on 2 tables."]


def test_drops_lead_with_the_writes_they_speed_up():
    writes = [{"key": "w1", "name": "INSERT on readings", "share": 0.12},
              {"key": "w2", "name": "UPDATE on readings", "share": 0.03}]
    story = index_story(_plan(observed_s=5 * HOUR,
                              drop=[{**_drop("a"), "slows": writes}, {**_drop("b"), "slows": writes[:1]}]))
    assert story["lead"] == ["Drop 2 unused indexes on readings to speed up 2 writes that take "
                             "15% of database time."]
    assert story["details"][1].startswith(
        "Every run of INSERT on readings and UPDATE on readings also updates these indexes. "
        "After you drop them, RDST measures those writes before and after.")

