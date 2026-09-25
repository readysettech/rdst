"""Impact for ranking queries: live calls times a cost per call that starts from the
engine's long-run average and moves to the live one as calls build up.

A few minutes of readings count calls well but average cost poorly: one slow call swings a
query that ran a dozen times. The engine's running totals hold thousands of calls, so their
average steadies the ranking until live calls outweigh it. A query that isn't running has
no live calls and no impact, whatever its history.
"""

# Live calls that weigh as much as the long-run average.
PRIOR_CALLS = 50


def blended_ms(calls: float, total_ms: float, prior: tuple[int, float] | None) -> float:
    """Ranking weight for `calls` live calls taking `total_ms`, given the engine's running
    (calls, ms) for the same query."""
    if calls <= 0:
        return 0.0
    if not prior or prior[0] <= 0:
        return total_ms
    long_run = prior[1] / prior[0]
    return calls * (total_ms + PRIOR_CALLS * long_run) / (calls + PRIOR_CALLS)
