"""The upgrade invitation belongs to an install, independently of UI storage."""

import toml

from features.init.service import InitService
from shared.config.targets import TargetsConfig


def test_fresh_install_stays_ineligible_after_target_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    cfg = TargetsConfig()
    cfg.load()
    cfg._data["targets"]["new"] = {"engine": "postgresql"}
    cfg.save()
    assert InitService().monitoring_upgrade() == {"targets": []}


def test_upgrade_captures_existing_targets_and_dismissal_survives_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    path = tmp_path / "config.toml"
    path.write_text(toml.dumps({"init": {"completed": True}, "targets": {
        "old": {"engine": "mysql"}}}))
    # A new target added before opening the UI doesn't become an upgrade target.
    cfg = TargetsConfig()
    cfg.load()
    cfg._data["targets"]["new"] = {"engine": "postgresql"}
    cfg.save()
    expected = {"targets": [{"name": "old", "engine": "mysql"}]}
    assert InitService().monitoring_upgrade() == expected
    assert InitService().monitoring_upgrade() == expected
    InitService().dismiss_monitoring_upgrade()
    assert InitService().monitoring_upgrade() == {"targets": []}
    assert toml.load(path)["init"]["completed"] is True


def test_old_empty_install_never_becomes_eligible(tmp_path, monkeypatch):
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text('[init]\ncompleted = false\n')
    assert InitService().monitoring_upgrade() == {"targets": []}
    cfg = TargetsConfig()
    cfg.load()
    cfg._data["targets"]["new"] = {"engine": "postgresql"}
    cfg.save()
    assert InitService().monitoring_upgrade() == {"targets": []}
