from types import SimpleNamespace as S
import pytest
from features.ask.endpoint_request import accepts_endpoint_request

Q = "Count undirected edges incident to local node 12 across all graphs."
GOOD = {
    "status": "clear",
    "count_unit": "undirected_relationships",
    "result_shape": "scalar_count",
    "endpoint_number": "12",
    "count_phrase": "Count undirected edges",
    "conditions": [
        {"role": "endpoint_number", "phrase": "local node 12"},
        {"role": "all_graphs", "phrase": "across all graphs"},
    ],
}


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"status": "unknown"},
        {"count_unit": "graph_namespaces"},
        {"count_unit": "directed_records"},
        {"result_shape": "grouped_counts"},
        {"endpoint_number": "112"},
        {"endpoint_number": None},
        {"count_phrase": "imagined"},
        {"conditions": []},
        {"conditions": [{"role": "all_graphs", "phrase": "across all graphs"}]},
        {
            "conditions": GOOD["conditions"]
            + [{"role": "edge_property", "phrase": "undirected"}]
        },
        {
            "conditions": GOOD["conditions"]
            + [{"role": "endpoint_number", "phrase": "local node 12"}]
        },
        {"extra": True},
    ],
)
def test_exact_question_inventory(change):
    assert accepts_endpoint_request(Q, S(number="12"), GOOD | change) == (not change)
