"""Tests for liquifai.pipeline.ConfigPipeline."""

from liquifai.pipeline import ConfigPipeline


def test_config_pipeline_load() -> None:
    raw = {"name": "test_app", "env_path": "$HOME/data"}
    pipeline = ConfigPipeline.load(raw)
    assert isinstance(pipeline.data, dict)
    assert pipeline.data["name"] == "test_app"


def test_config_pipeline_bind_positionals() -> None:
    data = {"existing": "val"}
    pipeline = ConfigPipeline(data).bind_positionals(["pos1", "pos2"], ["arg1", "arg2"])
    assert pipeline.data["pos1"] == "arg1"
    assert pipeline.data["pos2"] == "arg2"
    assert pipeline.data["existing"] == "val"


def test_config_pipeline_apply_overrides() -> None:
    data = {"lr": 0.01, "nested": {"param": 10}}
    pipeline = ConfigPipeline(data).apply_overrides({"lr": 0.001, "nested.param": 20}, [])
    assert pipeline.data["lr"] == 0.001
    assert pipeline.data["nested"]["param"] == 20


def test_config_pipeline_apply_deletions() -> None:
    data = {"lr": 0.01, "to_delete": "yes"}
    pipeline = ConfigPipeline(data).apply_overrides({}, ["to_delete"])
    assert "to_delete" not in pipeline.data
