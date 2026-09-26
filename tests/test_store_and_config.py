"""持久化与配置：原子写入、日志分段、配置校验。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from rubbercure.config import (
    PlatformConfig,
    SteamLimits,
    config_to_mapping,
    default_config,
    load_config,
)
from rubbercure.errors import ConfigError, StorageError
from rubbercure.store.journal import JsonlJournal
from rubbercure.store.jsonfile import JsonStore

ROOT = Path(__file__).resolve().parents[1]


def test_json_store_roundtrip_and_digest(tmp_path):
    store = JsonStore(str(tmp_path / "state"))
    assert store.read("missing") is None
    assert store.read("missing", {"default": True}) == {"default": True}
    digest = store.write("doc", {"b": 2, "a": 1})
    assert store.exists("doc")
    assert store.read("doc") == {"a": 1, "b": 2}
    assert store.digest("doc") == digest
    assert store.remove("doc") is True
    assert store.remove("doc") is False
    assert store.digest("doc") == ""


def test_json_store_rejects_illegal_keys(tmp_path):
    store = JsonStore(str(tmp_path / "state"))
    with pytest.raises(StorageError):
        store.path_for("dir/inner")
    with pytest.raises(StorageError):
        store.path_for("")


def test_journal_rolls_segments_and_reads_in_order(tmp_path):
    journal = JsonlJournal(str(tmp_path / "state"), "audit", segment_limit=2)
    for index in range(5):
        journal.append({"seq": index})
    assert journal.segment_count() == 3
    records = journal.read_all()
    assert [record["seq"] for record in records] == [0, 1, 2, 3, 4]
    assert len(journal.read_segment(0)) == 2
    assert journal.read_segment(99) == []


def test_journal_rejects_non_positive_segment_limit(tmp_path):
    with pytest.raises(StorageError):
        JsonlJournal(str(tmp_path / "state"), "audit", segment_limit=0)


def test_load_config_from_repository_file():
    config = load_config(str(ROOT / "config" / "rubbercure.yaml"))
    assert config.name == "RubberCure"
    assert config.press.fleet_size == 3
    assert config.steam.min_pressure_kpa < config.steam.target_pressure_kpa
    mapping = config_to_mapping(config)
    assert mapping["limits"]["steam"]["target_pressure_kpa"] == config.steam.target_pressure_kpa
    assert PlatformConfig.from_mapping(mapping).name == config.name


def test_load_config_reports_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "absent.yaml"))


def test_config_rejects_missing_section_and_bad_window(tmp_path):
    with pytest.raises(ConfigError):
        PlatformConfig.from_mapping({})
    with pytest.raises(ConfigError):
        SteamLimits.from_mapping(
            {
                "target_pressure_kpa": 700.0,
                "min_pressure_kpa": 80.0,
                "max_pressure_kpa": 500.0,
                "safe_residual_kpa": 12.0,
                "purge_hold_s": 20.0,
                "purge_token_max_age_s": 180.0,
                "vent_hold_s": 8.0,
                "valve_rated_flow_kg_h": 960.0,
                "trap_rated_flow_kg_h": 240.0,
            }
        )


def test_default_config_storage_root_is_relative():
    config = default_config()
    assert not os.path.isabs(config.storage_root)
    assert config.steam.clamp_pressure(10_000.0) == config.steam.max_pressure_kpa
    assert config.steam.clamp_pressure(-5.0) == config.steam.min_pressure_kpa
