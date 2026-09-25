"""Large monitor query families share naming work and preserve labels."""
from shared.monitor import statements


def test_large_family_has_bounded_detail_scans_and_distinct_names(monkeypatch):
    scanned = 0

    class CountedTuple(tuple):
        def __iter__(self):
            nonlocal scanned
            for value in super().__iter__():
                scanned += 1
                yield value

    original = statements._name_details

    def details(sql):
        return tuple(CountedTuple(values) for values in original(sql))

    monkeypatch.setattr(statements, "_name_details", details)
    texts = {f"q{index}": f"SELECT c{index} FROM users" for index in range(500)}
    names = statements.distinct_names(texts)
    assert names == {key: f"SELECT on users, returning c{index}"
                     for index, key in enumerate(texts)}
    # Count traversed values so the work bound is independent of machine speed.
    assert scanned < len(texts) * 20
    assert statements.distinct_names(dict(reversed(list(texts.items()))), names) == names
