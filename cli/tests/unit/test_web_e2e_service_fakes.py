"""Pin the browser-test fakes to the production services they stand in for.

Every fake must subclass its real service and every overridden method must
keep the production signature, so drift in a service's route-facing surface
fails here at unit-test speed instead of surfacing (or not) in a browser run.
"""

import importlib
import inspect

import pytest


@pytest.fixture()
def fakes(monkeypatch, tmp_path):
    monkeypatch.setenv("RDST_E2E_HOME", str(tmp_path))
    import tests.web_e2e.fakes as fakes_module

    return importlib.reload(fakes_module)


def _signature_fingerprint(func):
    return [
        (param.name, param.kind, param.default)
        for param in inspect.signature(func).parameters.values()
    ]


def _overridden_methods(fake):
    for name, member in vars(fake).items():
        if not name.startswith("_") and inspect.isfunction(member):
            yield name, member


def test_every_fake_subclasses_a_production_service(fakes):
    for fake in fakes.SERVICE_FAKES:
        bases = [base for base in fake.__bases__ if base is not object]
        assert len(bases) == 1, f"{fake.__name__} must subclass one real service"
        assert bases[0].__module__.startswith("features."), (
            f"{fake.__name__} must subclass the production service, "
            f"got {bases[0].__module__}"
        )


def test_overridden_methods_exist_on_the_real_service(fakes):
    for fake in fakes.SERVICE_FAKES:
        real = fake.__bases__[0]
        for name, _ in _overridden_methods(fake):
            assert hasattr(real, name), (
                f"{fake.__name__}.{name} does not exist on {real.__name__}; "
                "the production route would bypass the fake"
            )


def test_overridden_methods_keep_production_signatures(fakes):
    for fake in fakes.SERVICE_FAKES:
        real = fake.__bases__[0]
        for name, member in _overridden_methods(fake):
            real_member = getattr(real, name)
            assert _signature_fingerprint(member) == _signature_fingerprint(
                real_member
            ), f"{fake.__name__}.{name} signature drifted from {real.__name__}.{name}"


def test_overridden_methods_keep_production_calling_convention(fakes):
    for fake in fakes.SERVICE_FAKES:
        real = fake.__bases__[0]
        for name, member in _overridden_methods(fake):
            real_member = getattr(real, name)
            assert inspect.isasyncgenfunction(member) == inspect.isasyncgenfunction(
                real_member
            ), f"{fake.__name__}.{name} async-generator kind drifted"
            assert inspect.iscoroutinefunction(member) == inspect.iscoroutinefunction(
                real_member
            ), f"{fake.__name__}.{name} coroutine kind drifted"


def test_autocomplete_fake_matches_the_real_collector(fakes):
    from features.schema.schema_collector import collect_all_tables_schema

    assert _signature_fingerprint(fakes.fake_autocomplete_schema) == (
        _signature_fingerprint(collect_all_tables_schema)
    )
