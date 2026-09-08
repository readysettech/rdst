import asyncio

import pytest

from devtools.ask_benchmark.runner import ask_profile_service_flags
from features.ask.correction_intent_routing import CORRECTION_INTENT_EXPERIMENTAL_SCOPE
from features.ask.profiles import default_ask_service_options
from features.ask.service import AskService


def test_product_uses_complete_candidate_v4_profile():
    options = default_ask_service_options()
    flags = ask_profile_service_flags("candidate-v4")
    assert {key: options[key] for key in flags} == flags
    assert options["enum_overlap_advisory"] is True
    assert (
        options["correction_intent_routing_intent_scope"]
        == CORRECTION_INTENT_EXPERIMENTAL_SCOPE
    )
    service = AskService(**options)
    for key, value in flags.items():
        assert getattr(service, "_" + key) == value
    options["correction_intent_routing_enabled"] = False
    assert default_ask_service_options()["correction_intent_routing_enabled"] is True


@pytest.mark.parametrize("entrypoint", ["api", "cli"])
def test_product_entrypoints_select_candidate_v4(monkeypatch, entrypoint):
    from features.ask.api import routes
    from features.ask.cli.command import AskCommand
    from features.ask import service

    captured = []

    def capture_service(**kwargs):
        captured.append(kwargs)
        raise RuntimeError("Stop before contacting a database or model")

    if entrypoint == "api":
        monkeypatch.setattr(routes, "AskService", capture_service)

        async def collect():
            return [event async for event in routes._ask_generator(None, None)]

        asyncio.run(collect())
    else:
        monkeypatch.setattr(service, "AskService", capture_service)
        AskCommand()._execute_impl(question="Count items", no_interactive=True)

    assert captured == [default_ask_service_options()]
